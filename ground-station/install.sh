#!/bin/bash
# install.sh - UMB Ground Station installer
# Works standalone or as part of Packer image build.
# Usage: curl -fsSL https://raw.githubusercontent.com/.../install.sh | sudo bash
set -euo pipefail

UMBGS_VERSION="${UMBGS_VERSION:-latest}"
GITHUB_REPO="EricAndrechek/umich-balloons"
DATA_DIR="/data"
SYSTEMD_DIR="/etc/systemd/system"

log() { echo "[umbgs-install] $*"; }
err() { echo "[umbgs-install] ERROR: $*" >&2; exit 1; }

# Must be root
[ "$(id -u)" -eq 0 ] || err "Must run as root"

log "=== UMB Ground Station Installer ==="
log "Version: $UMBGS_VERSION"

# ─── System packages ───────────────────────────────────────────────
log "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    direwolf \
    gpsd gpsd-clients \
    chrony \
    network-manager \
    cage \
    cog \
    jq \
    curl

# ─── RTL-SDR from source (rtlsdrblog fork for V4/V5 support) ──────
if ! command -v rtl_test &>/dev/null; then
    log "Building RTL-SDR drivers from source..."
    apt-get install -y -qq git cmake build-essential libusb-1.0-0-dev
    echo 'blacklist dvb_usb_rtl28xxu' > /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf

    RTLSDR_DIR=$(mktemp -d)
    git clone --depth 1 https://github.com/rtlsdrblog/rtl-sdr-blog "$RTLSDR_DIR"
    cd "$RTLSDR_DIR"
    mkdir build && cd build
    cmake ../ -DINSTALL_UDEV_RULES=ON
    make -j"$(nproc)"
    make install
    cp ../rtl-sdr.rules /etc/udev/rules.d/
    ldconfig
    cd /
    rm -rf "$RTLSDR_DIR"
    log "RTL-SDR drivers installed"
else
    log "RTL-SDR already installed, skipping"
fi

# ─── Data partition ────────────────────────────────────────────────
log "Setting up data directory..."
mkdir -p "$DATA_DIR"

# ─── Download binary ──────────────────────────────────────────────
log "Downloading umbgs binary..."
ARCH=$(dpkg --print-architecture)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    ARCH="arm64"
fi

if [ "$UMBGS_VERSION" = "latest" ]; then
    DOWNLOAD_URL="https://github.com/${GITHUB_REPO}/releases/latest/download/umbgs-linux-${ARCH}"
else
    DOWNLOAD_URL="https://github.com/${GITHUB_REPO}/releases/download/${UMBGS_VERSION}/umbgs-linux-${ARCH}"
fi

curl -fsSL -o "${DATA_DIR}/umbgs-a" "$DOWNLOAD_URL"
chmod +x "${DATA_DIR}/umbgs-a"
echo "a" > "${DATA_DIR}/active"
ln -sf "${DATA_DIR}/umbgs-a" "${DATA_DIR}/umbgs"
log "Binary installed to ${DATA_DIR}/umbgs-a"

# ─── Default config ──────────────────────────────────────────────
if [ ! -f /boot/firmware/ground-station.yaml ]; then
    log "Creating default config on boot partition..."
    cat > /boot/firmware/ground-station.yaml << 'YAML'
# UMB Ground Station Configuration
# Edit this file and reboot, or use the web dashboard.

callsign: "CHANGE_ME"
ssid: 9
api_url: "https://api.umich-balloons.com"

wifi:
  networks:
    - ssid: ""
      psk: ""

aprs:
  enabled: true
  kiss_host: "127.0.0.1"
  kiss_port: 8001
  frequency: 144.390
  gain: 42

lora:
  enabled: true
  baud: 9600

gps:
  enabled: true
  report_interval: 60

dashboard:
  enabled: true
  port: 8080

display:
  enabled: false
  url: "http://localhost:8080"

update:
  enabled: true
  channel: "stable"

log_level: "info"
YAML
fi

# ─── Systemd units ────────────────────────────────────────────────
log "Installing systemd units..."

# Copy service files (use heredocs for standalone install)
cat > "${SYSTEMD_DIR}/umbgs.service" << 'EOF'
[Unit]
Description=UMB Ground Station
After=network-online.target gpsd.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/data/umbgs
Restart=always
RestartSec=5
WatchdogSec=120
Environment=GOGC=50
StandardOutput=journal
StandardError=journal
SyslogIdentifier=umbgs
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/data /boot/firmware
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

cat > "${SYSTEMD_DIR}/direwolf.service" << 'EOF'
[Unit]
Description=Direwolf APRS TNC
After=sound.target

[Service]
Type=simple
ExecStart=/usr/bin/direwolf -t 0
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=direwolf

[Install]
WantedBy=multi-user.target
EOF

cat > "${SYSTEMD_DIR}/umbgs-watchdog.service" << 'EOF'
[Unit]
Description=UMB Ground Station Watchdog
After=umbgs.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/umbgs-watchdog.sh
EOF

cat > "${SYSTEMD_DIR}/umbgs-watchdog.timer" << 'EOF'
[Unit]
Description=UMB Ground Station Watchdog Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

# Install watchdog script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/watchdog.sh" ]; then
    cp "${SCRIPT_DIR}/watchdog.sh" /usr/local/bin/umbgs-watchdog.sh
else
    # Download watchdog if not available locally
    curl -fsSL -o /usr/local/bin/umbgs-watchdog.sh \
        "https://raw.githubusercontent.com/${GITHUB_REPO}/main/ground-station/watchdog.sh"
fi
chmod +x /usr/local/bin/umbgs-watchdog.sh

# ─── Enable services ─────────────────────────────────────────────
log "Enabling services..."
systemctl daemon-reload
systemctl enable gpsd
systemctl enable direwolf
systemctl enable umbgs
systemctl enable umbgs-watchdog.timer

# ─── Chrony GPS config ───────────────────────────────────────────
if ! grep -q "SHM 0" /etc/chrony/chrony.conf 2>/dev/null; then
    log "Configuring chrony for GPS time..."
    cat >> /etc/chrony/chrony.conf << 'EOF'

# GPS via gpsd shared memory
refclock SHM 0 offset 0.5 delay 0.2 refid NMEA noselect
refclock SHM 1 offset 0.0 delay 0.01 refid PPS prefer
EOF
    systemctl restart chrony 2>/dev/null || true
fi

# ─── NetworkManager config ────────────────────────────────────────
log "Configuring NetworkManager..."
cat > /etc/NetworkManager/conf.d/umbgs.conf << 'EOF'
[main]
plugins=keyfile

[connection]
wifi.powersave=2

[connectivity]
uri=http://nmcheck.gnome.org/check_network_status.txt
interval=60
EOF

# ─── System hardening ─────────────────────────────────────────────
log "Applying system hardening..."

# Disable swap to protect SD card
systemctl disable --now dphys-swapfile 2>/dev/null || true
swapoff -a 2>/dev/null || true

# Set hostname pattern
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" = "raspberrypi" ] || [ "$CURRENT_HOSTNAME" = "localhost" ]; then
    hostnamectl set-hostname "umb-ground-station"
fi

# ─── Overlayfs read-only root ─────────────────────────────────────
log "Configuring overlayfs for read-only root filesystem..."
mkdir -p /data/logs

# Create overlayfs setup script that runs at boot
cat > /usr/local/bin/umbgs-overlayfs.sh << 'OVERLAYEOF'
#!/bin/bash
# Enable overlayfs on root filesystem.
# /data/ partition stays read-write. Root becomes read-only with
# an overlay backed by tmpfs so runtime changes are discarded on reboot.
set -euo pipefail

FSTAB="/etc/fstab"

# Check if already configured
if grep -q "overlay" "$FSTAB" 2>/dev/null; then
    exit 0
fi

# Ensure /data is a separate entry in fstab (may already be from partition setup)
if ! grep -q "/data" "$FSTAB"; then
    # Find root device and add /data bind mount
    ROOT_DEV=$(findmnt -n -o SOURCE /)
    echo "${ROOT_DEV} /data ext4 defaults,noatime 0 2" >> "$FSTAB"
fi

# Make root read-only by adding 'ro' option
sed -i 's|\(.*\s/\s.*\)defaults\(.*\)|\1defaults,ro\2|' "$FSTAB"

# Create overlay directories
mkdir -p /data/overlay/upper /data/overlay/work

# Add tmpfs overlays for directories that need runtime writes
cat >> "$FSTAB" << 'EOF'
# Overlayfs writable layers for read-only root
tmpfs /tmp tmpfs defaults,nosuid,nodev,size=64M 0 0
tmpfs /var/tmp tmpfs defaults,nosuid,nodev,size=32M 0 0
overlay /etc overlay defaults,lowerdir=/etc,upperdir=/data/overlay/upper/etc,workdir=/data/overlay/work/etc 0 0
overlay /var/log overlay defaults,lowerdir=/var/log,upperdir=/data/overlay/upper/var-log,workdir=/data/overlay/work/var-log 0 0
EOF

# Create overlay subdirectories
mkdir -p /data/overlay/upper/etc /data/overlay/work/etc
mkdir -p /data/overlay/upper/var-log /data/overlay/work/var-log

# Ensure /data/logs persists (symlink from overlay)
mkdir -p /data/logs

echo "Overlayfs configured. Reboot required to take effect."
OVERLAYEOF
chmod +x /usr/local/bin/umbgs-overlayfs.sh

# Create systemd unit to run overlayfs setup once
cat > "${SYSTEMD_DIR}/umbgs-overlayfs-setup.service" << 'EOF'
[Unit]
Description=UMB Ground Station Overlayfs Setup (one-shot)
ConditionPathExists=!/data/.overlayfs-configured
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/umbgs-overlayfs.sh
ExecStartPost=/usr/bin/touch /data/.overlayfs-configured
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl enable umbgs-overlayfs-setup.service

log "=== Installation complete ==="
log "Next steps:"
log "  1. Edit /boot/firmware/ground-station.yaml with your callsign and WiFi"
log "  2. Reboot: sudo reboot"
log "  3. Dashboard will be at http://<ip>:8080"
