# Ground Station — Remaining Work

Last refreshed: 2026-04-10. See `CLAUDE.md` for build/deploy/architecture reference.

The bulk of the rewrite is done — umbgs runs all subsystems reliably on the live Pi, the dashboard works, direwolf/RTL-SDR/APRS-IS/LoRa/GPS/WiFi/hotspot/updater/watchdog/config editor/logs are all functional. This file tracks only what's still open.

---

## Open Items

### Plymouth splash screen (unresolved)
Splash image still doesn't render on boot. Current behavior: a black screen appears before the kiosk loads (progress from nothing at all, but still wrong — should show `assets/splash.png`).

- `install.sh` copies the theme and runs `plymouth-set-default-theme -R pix` (or defers to firstboot in chroot).
- Kernel cmdline has `splash logo.nologo vt.global_cursor_default=0`.
- `firstboot.sh` rebuilds initramfs on real hardware.

Things to check next time:
1. `lsinitramfs /boot/initrd.img-$(uname -r) | grep plymouth` on the Pi — is the theme actually in initramfs?
2. `plymouth --help` / `plymouth-set-default-theme -l` — is `pix` registered?
3. `plymouthd --debug` during boot — what is it actually doing?
4. The MPI7002 touchscreen's 1920x1080 EDID may also confuse Plymouth's mode-setting. vc4 is rejecting custom modes (`User-defined mode not supported` in dmesg).

Files: `install.sh` (Plymouth section), `firstboot.sh`, `config/pix.plymouth`, `config/pix.script`, `assets/splash.png`.

---

### LoRa stable device naming
Auto-detect can still pick the wrong `/dev/ttyUSB*` on first boot. Better error detection and backoff are in place, but the real fix is a udev rule that creates a stable symlink (e.g. `/dev/ttyLoRa`) based on the LoRa module's USB VID:PID.

Tasks:
1. SSH to Pi, run `udevadm info /dev/ttyUSB0` and `/dev/ttyUSB1` to get idVendor/idProduct for the LoRa module.
2. Add `config/99-umbgs-lora.rules` with a `SYMLINK+="ttyLoRa"` entry.
3. Install it from `install.sh`.
4. Default `lora.device` to `/dev/ttyLoRa` in the config.

Files: `umbgs/internal/lora/lora.go`, `umbgs/internal/config/config.go`, `install.sh`.

---

### End-to-end uploader test against real API
The QUIC/HTTP uploader is implemented and exercised in unit tests, but hasn't been verified against `https://api.umich-balloons.com` with real packet data.

Tasks:
1. Capture a real APRS or LoRa packet through the pipeline.
2. Watch the API logs for the request, verify msgpack+gzip decodes, verify buffer drain after an offline→online transition.
3. Confirm 405/5xx handling behaves sensibly.

File: `umbgs/internal/uploader/uploader.go`.

---

### Packer build — end-to-end verification
`packer/ground-station.json` was simplified (shell-local cross-compile → `UMBGS_BINARY` env var → `install.sh`). Structurally correct but never run end-to-end:

1. Run `packer build` locally with Docker (see README.md).
2. Flash resulting image to a fresh SD card.
3. Boot a clean Pi, verify umbgs starts, dashboard works, AP hotspot comes up, direwolf + LoRa + GPS all connect.
4. Fix anything that breaks in the clean-install path.

Known concern: RTL-SDR driver source build inside the QEMU chroot may not find kernel headers. If so, move the udev rules install inline and skip the cmake build in chroot (install the rtlsdrblog fork via apt once it lands, or fall back to the stock `librtlsdr`).

Files: `packer/ground-station.json`, `install.sh`.

---

### Test coverage expansion
Current tests: `config` (49.4%), `dashboard/logs` (18.6%), `direwolf/passcode` (2 tests). Good starting point but sparse. Candidates for more coverage:
- `internal/buffer` — SQLite packet buffer correctness, crash recovery
- `internal/uploader` — retry/backoff, offline→online drain, msgpack round-trip
- `internal/direwolf/config` — template rendering with edge-case callsigns
- `internal/updater` — A/B slot swap logic

Not urgent — the project runs on a hobby timeline and the existing tests cover the config layer that used to cause most of the bugs.

---

### CI/CD release pipeline
`.github/workflows/ground-station.yml` runs `go vet` and `go test` and cross-compiles. Still missing:
1. A release job that attaches the binary to a GitHub tag.
2. A Packer image build job that attaches the `.img.xz` to the same release.
3. Integration with the watchdog rollback (`watchdog.sh`) — verify that if a new binary crashes on boot, the A/B slot actually reverts.

---

### Dashboard polish (low priority)
- Verify log viewer UX on the 7" kiosk screen end-to-end (level badges, jump arrows, custom scrollbars). Works on laptop; not re-verified on the touchscreen since the CSS zoom bump to 2.5.
- Mobile/phone layout is functional but un-optimized.
- `umbgs.service` runs as root. Fine for an appliance, but could drop to non-root + capabilities later.

---

## Quick Reference: What's Working

Verified on `192.168.0.207` as of 2026-04-10:

- umbgs binary runs all subsystems, systemd WatchdogSec=120 confirmed working (NOTIFY_SOCKET + WATCHDOG_USEC propagated).
- Dashboard on `:8080` with live stats, logs, packets, config editor, admin.
- Config editor with WiFi, APRS, LoRa, GPS, Display sections; optimistic concurrency; WS broadcast.
- RTL-SDR → `rtl_fm | direwolf` subprocess pipeline, clean shutdown via process group kills.
- Direwolf APRS-IS connected, TBEACON every 30 min from gpsd (dwgpsd will time out until GPS fix — expected).
- APRS KISS TCP listener on port 8001.
- LoRa serial reader (auto-detect still flaky on first boot — see open items).
- GPS via gpsd.
- NetworkManager WiFi sync + AP hotspot (`UMB-<CALLSIGN>`) — polkit rule now grants root full NM access.
- Connectivity monitor (per-interface IP, SSID, signal via NM D-Bus).
- Cage + cog kiosk, XCURSOR_THEME=blank + system default fallback hides the pointer on the touchscreen.
- CSS zoom 2.5 compensates for the MPI7002's 1920x1080 EDID on a physical 1024x600 panel.
- `--version` flag; version stamped via `-ldflags -X main.version=...`.
- A/B binary slot updater; watchdog rollback script.
