# Ground Station

Single Go binary (`umbgs`) that runs on a Raspberry Pi (4/5, or Le Potato) to receive APRS and LoRa balloon telemetry and upload it to the API. Replaces the previous multi-service Python setup.

## What It Does

- **APRS**: Connects to Direwolf's KISS TCP port, reads decoded packets
- **LoRa**: Auto-detects USB serial (Arduino/ESP32), reads JSON telemetry
- **GPS**: Connects to gpsd, reports ground station position to API (`/station` endpoint) at configurable intervals
- **Upload**: MessagePack + gzip over QUIC/HTTP/3 (falls back to HTTP/2). Immediate upload per packet — buffers to SQLite only on failure
- **Dashboard**: Embedded web UI at port 8080 with live stats, packet feed, logs, config editor, and manual update trigger via WebSocket
- **Update**: A/B binary slots with watchdog rollback — manual trigger only (no auto-polling)
- **Network**: Syncs WiFi from config via NetworkManager, creates AP hotspot `UMB-<CALLSIGN>` as fallback
- **LED**: Pi activity LED shows status (booting/online/offline/uploading/error)

## Architecture

```txt
┌───────────────────────────────────────────────────┐
│                    umbgs binary                   │
│                                                   │
│  ┌─────────┐  ┌──────┐  ┌─────┐                   │
│  │  APRS   │  │ LoRa │  │ GPS │  listeners        │
│  └────┬────┘  └──┬───┘  └──┬──┘                   │
│       └──────────┼─────────┘                      │
│              ┌───▼────┐                           │
│              │Uploader│──→ API (QUIC/msgpack/gzip)│
│              └───┬────┘                           │
│              ┌───▼────┐                           │
│              │ SQLite │  offline buffer           │
│              └────────┘                           │
│  ┌──────────┐  ┌─────────┐  ┌─────────┐           │
│  │Dashboard │  │ Updater │  │ Network │           │
│  │ :8080    │  │ A/B slot│  │ NM D-Bus│           │
│  └──────────┘  └─────────┘  └─────────┘           │
└───────────────────────────────────────────────────┘
    External: Direwolf ← RTL-SDR ← radio
              gpsd ← USB GPS
```

## Prerequisites

- Go 1.22+ (for building)
- Target: Linux arm64 (Raspberry Pi OS Bookworm Lite)
- Hardware: RTL-SDR dongle, USB GPS, optional LoRa serial device

## Building

### Cross-compile on Mac (or any host)

```bash
cd ground-station/umbgs
GOOS=linux GOARCH=arm64 go build -ldflags "-s -w -X main.version=dev" -o umbgs ./cmd/umbgs/
```

### Build natively on a Pi

```bash
cd ground-station/umbgs
go build -ldflags "-s -w -X main.version=dev" -o umbgs ./cmd/umbgs/
```

The binary is fully static — no CGo, no external `.so` dependencies.

## Deploying to a Pi

### Option A: Fresh image with Packer (recommended for new Pis)

Packer builds a complete SD card image with all dependencies pre-installed. This runs in CI automatically on GitHub Release, or you can build locally:

```bash
# Requires Docker (Packer ARM builder runs inside Docker)
# Run from the repo root
docker run --rm --privileged \
  -v /dev:/dev \
  -v $(pwd):/build \
  mkaczanowski/packer-builder-arm:latest \
  build \
  -var "umbgs_version=dev" \
  /build/ground-station/packer/ground-station.json

# Output: ground-station/packer/output/umb-ground-station-<date>.img.gz
# Flash to SD card:
gunzip -k output/umb-ground-station-*.img.gz
# On Mac:
diskutil list                          # find your SD card (e.g., /dev/disk4)
diskutil unmountDisk /dev/disk4
sudo dd if=output/umb-ground-station-*.img of=/dev/rdisk4 bs=4m status=progress
diskutil eject /dev/disk4
```

> **Note:** The Packer build requires `--privileged` Docker for loopback device access. On macOS this works via Docker Desktop. The `packer-builder-arm` image handles QEMU aarch64 emulation automatically.

### Option B: Install on existing Pi OS

```bash
# 1. Flash Raspberry Pi OS Lite (64-bit/arm64, Bookworm) to SD card using rpi-imager
# 2. Boot the Pi, SSH in
# 3. Copy files to Pi
scp ground-station/install.sh pi@<pi-ip>:~/
scp ground-station/umbgs/umbgs pi@<pi-ip>:~/umbgs-binary
scp ground-station/ground-station.yaml pi@<pi-ip>:~/

# 4. On the Pi:
ssh pi@<pi-ip>

# Edit config FIRST — set your callsign and WiFi
nano ~/ground-station.yaml
sudo cp ~/ground-station.yaml /boot/firmware/ground-station.yaml

# Run the installer (installs deps, systemd units, creates /data partition)
sudo bash ~/install.sh

# Copy binary to the data partition
sudo cp ~/umbgs-binary /data/umbgs-a
sudo chmod +x /data/umbgs-a
sudo ln -sf /data/umbgs-a /data/umbgs

# Start it
sudo systemctl start umbgs
sudo journalctl -u umbgs -f
```

### Option C: Manual testing (no install script)

```bash
# Copy binary and config to Pi
scp umbgs pi@<pi-ip>:/tmp/
scp ground-station.yaml pi@<pi-ip>:/tmp/

ssh pi@<pi-ip>

# Install just the essentials
sudo apt update && sudo apt install -y direwolf gpsd chrony

# Put config where umbgs expects it
sudo mkdir -p /boot/firmware
sudo cp /tmp/ground-station.yaml /boot/firmware/

# Create data dir
sudo mkdir -p /data

# Run directly (Ctrl+C to stop)
sudo /tmp/umbgs 2>&1 | jq .
```

## Configuration

Config lives at `/boot/firmware/ground-station.yaml` — readable from any OS by mounting the FAT32 boot partition. See [ground-station.yaml](ground-station.yaml) for the annotated template.

Key settings to change:

- `callsign`: Your FCC amateur radio callsign (required)
- `wifi.networks`: WiFi SSID/password pairs
- `aprs.frequency`: 144.390 MHz for North America

The dashboard at `http://<pi-ip>:8080` has a Config tab for live editing.

## CI / Releases

GitHub Actions ([ground-station.yml](../.github/workflows/ground-station.yml)) handles:

1. **Build** — `go vet`, `go test`, cross-compile on every push/PR touching `ground-station/umbgs/`
2. **Release** — uploads `umbgs-linux-arm64` binary + SHA256 to GitHub Release
3. **Image** — builds Packer SD card image, uploads `.img.gz` to GitHub Release

To release: create a GitHub Release (tag like `v0.1.0`). CI builds and attaches the binary and image.

## Updates

Updates are triggered **manually** from the dashboard (`POST /api/update`) or via `kill -USR1 <pid>` for debug snapshots. There is no automatic polling.

Update flow:

1. User clicks "Check for Update" in dashboard (or `curl -X POST http://localhost:8080/api/update`)
2. Downloads new binary to inactive A/B slot (e.g., `/data/umbgs-b`)
3. Verifies SHA256
4. Writes `pending` file, switches `active` pointer, restarts
5. Watchdog timer (every 5 min) checks: if `pending` file is >10 min old, rolls back to previous slot

Disable with `update.enabled: false` in config.

## Logging

- Structured JSON logs go to both **stdout** (journald) and `/data/logs/umbgs.log`
- Log file auto-rotates at 10 MB (keeps one `.1` backup)
- Send `SIGUSR1` to dump a debug snapshot to `/boot/firmware/debug-snapshot-<timestamp>.log` (readable from any computer via SD card FAT32 partition)

## Project Structure

```txt
ground-station/
├── assets/                  # Splash screen images
├── ground-station.yaml      # Default config template
├── install.sh               # Standalone Pi installer
├── firstboot.sh             # First-boot setup (rfkill, hostname)
├── watchdog.sh              # A/B rollback watchdog
├── config/                  # Config files copied by install.sh
│   ├── umbgs.service
│   ├── direwolf.service
│   ├── umbgs-watchdog.service
│   ├── umbgs-watchdog.timer
│   ├── umbgs-firstboot.service
│   ├── getty-autologin.conf
│   ├── networkmanager.conf
│   ├── chrony-gps.conf
│   ├── pix.plymouth
│   └── pix.script
├── packer/
│   └── ground-station.json      # Packer ARM image config
└── umbgs/                   # Go source
    ├── go.mod
    ├── cmd/umbgs/main.go    # Entrypoint + orchestrator
    └── internal/
        ├── aprs/            # Direwolf KISS listener
        ├── buffer/          # SQLite offline buffer
        ├── config/          # YAML config + hot reload
        ├── connectivity/    # NetworkManager D-Bus monitor
        ├── dashboard/       # Web UI + WebSocket + log aggregator
        ├── direwolf/        # direwolf.conf generator
        ├── gps/             # gpsd client
        ├── led/             # Activity LED control
        ├── lora/            # USB serial reader
        ├── network/         # WiFi sync + AP hotspot
        ├── system/          # CPU/RAM/temp/uptime stats
        ├── types/           # Shared data types
        ├── updater/         # A/B slot updater
        └── uploader/        # QUIC/HTTP + msgpack + gzip
```

## Troubleshooting

```bash
# Check service status
sudo systemctl status umbgs direwolf gpsd

# Live logs (structured JSON)
sudo journalctl -u umbgs -f | jq .

# Check which binary slot is active
cat /data/active

# Check if an update is pending
ls -la /data/pending

# Force rollback
sudo /usr/local/bin/umbgs-watchdog.sh

# Dashboard
open http://<pi-ip>:8080

# Check RTL-SDR is detected
rtl_test

# Check GPS
gpsmon
```
