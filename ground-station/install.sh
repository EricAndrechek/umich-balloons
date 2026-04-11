#!/bin/bash
# install.sh - UMB Ground Station installer
# Works standalone on a Pi OR inside a Packer QEMU chroot.
# All config files live in ground-station/config/ and are copied into place.
# Usage: curl -fsSL https://raw.githubusercontent.com/EricAndrechek/umich-balloons/refs/heads/main/ground-station/install.sh | sudo bash
set -euo pipefail

UMBGS_VERSION="${UMBGS_VERSION:-latest}"
GITHUB_REPO="EricAndrechek/umich-balloons"
GITHUB_RAW="https://raw.githubusercontent.com/${GITHUB_REPO}/main/ground-station"
DATA_DIR="/data"
SYSTEMD_DIR="/etc/systemd/system"

log() { echo "[umbgs-install] $*"; }
err() { echo "[umbgs-install] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "Must run as root"

# Detect chroot (Packer QEMU) vs real hardware
IN_CHROOT=false
if ischroot 2>/dev/null; then
    IN_CHROOT=true
elif [ "$(stat -c %d:%i / 2>/dev/null)" != "$(stat -c %d:%i /proc/1/root/. 2>/dev/null)" ] 2>/dev/null; then
    IN_CHROOT=true
fi

# Resolve directories for sibling files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null || echo /tmp)"

# Find config files: local repo checkout > /tmp (Packer upload) > download from GitHub
config_file() {
    local name="$1" dest="$2"
    if [ -f "${SCRIPT_DIR}/config/${name}" ]; then
        cp "${SCRIPT_DIR}/config/${name}" "$dest"
    elif [ -f "/tmp/config/${name}" ]; then
        cp "/tmp/config/${name}" "$dest"
    else
        curl -fsSL -o "$dest" "${GITHUB_RAW}/config/${name}"
    fi
}

# Same pattern for non-config sibling files
fetch_file() {
    local name="$1" dest="$2"
    if [ -f "${SCRIPT_DIR}/${name}" ]; then
        cp "${SCRIPT_DIR}/${name}" "$dest"
    elif [ -f "/tmp/${name}" ]; then
        cp "/tmp/${name}" "$dest"
    else
        curl -fsSL -o "$dest" "${GITHUB_RAW}/${name}"
    fi
}

log "=== UMB Ground Station Installer ==="
log "Version: $UMBGS_VERSION"
log "Environment: $(if $IN_CHROOT; then echo 'chroot/QEMU'; else echo 'real hardware'; fi)"

# ─── System packages ───────────────────────────────────────────────
log "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    direwolf \
    gpsd gpsd-tools \
    chrony \
    network-manager \
    plymouth plymouth-themes \
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

# ─── Install binary ──────────────────────────────────────────────
# UMBGS_BINARY env var allows using a pre-built local binary (for Packer builds).
if [ -n "${UMBGS_BINARY:-}" ] && [ -f "$UMBGS_BINARY" ]; then
    log "Installing local binary from $UMBGS_BINARY..."
    cp "$UMBGS_BINARY" "${DATA_DIR}/umbgs-a"
elif [ -f "${SCRIPT_DIR}/umbgs" ]; then
    log "Installing binary from repo checkout..."
    cp "${SCRIPT_DIR}/umbgs" "${DATA_DIR}/umbgs-a"
elif [ -f "/tmp/ground-station/umbgs" ]; then
    log "Installing binary from /tmp (Packer upload)..."
    cp "/tmp/ground-station/umbgs" "${DATA_DIR}/umbgs-a"
else
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
fi
chmod +x "${DATA_DIR}/umbgs-a"
echo "a" > "${DATA_DIR}/active"
ln -sf "${DATA_DIR}/umbgs-a" "${DATA_DIR}/umbgs"
log "Binary installed to ${DATA_DIR}/umbgs-a"

# ─── Default config ──────────────────────────────────────────────
if [ ! -f /boot/firmware/ground-station.yaml ]; then
    log "Creating default config on boot partition..."
    fetch_file "ground-station.yaml" /boot/firmware/ground-station.yaml
fi

# ─── Systemd units ────────────────────────────────────────────────
log "Installing systemd units..."
config_file "umbgs.service"            "${SYSTEMD_DIR}/umbgs.service"
config_file "umbgs-watchdog.service"   "${SYSTEMD_DIR}/umbgs-watchdog.service"
config_file "umbgs-watchdog.timer"     "${SYSTEMD_DIR}/umbgs-watchdog.timer"
config_file "umbgs-firstboot.service"  "${SYSTEMD_DIR}/umbgs-firstboot.service"
config_file "umbgs-wifi-enable.service" "${SYSTEMD_DIR}/umbgs-wifi-enable.service"

# Watchdog + firstboot scripts
fetch_file "watchdog.sh" /usr/local/bin/umbgs-watchdog.sh
chmod +x /usr/local/bin/umbgs-watchdog.sh
fetch_file "firstboot.sh" /usr/local/bin/umbgs-firstboot.sh
chmod +x /usr/local/bin/umbgs-firstboot.sh

# ─── Enable services ─────────────────────────────────────────────
log "Enabling services..."
systemctl daemon-reload
# NOTE: direwolf is managed by umbgs directly (rtl_fm | direwolf pipeline), not as a systemd service.
systemctl enable gpsd umbgs umbgs-watchdog.timer umbgs-firstboot.service umbgs-wifi-enable.service

# ─── Chrony GPS config ───────────────────────────────────────────
if ! grep -q "SHM 0" /etc/chrony/chrony.conf 2>/dev/null; then
    log "Configuring chrony for GPS time..."
    echo "" >> /etc/chrony/chrony.conf
    CHRONY_TMP=$(mktemp)
    config_file "chrony-gps.conf" "$CHRONY_TMP"
    cat "$CHRONY_TMP" >> /etc/chrony/chrony.conf
    rm -f "$CHRONY_TMP"
    systemctl restart chrony 2>/dev/null || true
fi

# ─── NetworkManager config ────────────────────────────────────────
log "Configuring NetworkManager..."
config_file "networkmanager.conf" /etc/NetworkManager/conf.d/umbgs.conf

# Polkit rule so the umbgs service (running as root) can manage NM without interactive auth
mkdir -p /etc/polkit-1/rules.d
config_file "99-umbgs-networkmanager.rules" /etc/polkit-1/rules.d/99-umbgs-networkmanager.rules

# ─── System hardening ─────────────────────────────────────────────
log "Applying system hardening..."

# Disable swap to protect SD card
systemctl disable --now dphys-swapfile 2>/dev/null || true
swapoff -a 2>/dev/null || true

mkdir -p /data/logs
if ! grep -q "tmpfs /tmp" /etc/fstab 2>/dev/null; then
    echo "tmpfs /tmp tmpfs defaults,nosuid,nodev,size=64M 0 0" >> /etc/fstab
fi

# ─── User setup ───────────────────────────────────────────────────
if ! id umbgs &>/dev/null; then
    log "Creating umbgs user..."
    groupadd -f gpio; groupadd -f spi; groupadd -f i2c
    useradd -m -G sudo,video,dialout,gpio,spi,i2c,plugdev -s /bin/bash umbgs
    echo 'umbgs:umbgs' | chpasswd
fi

# Tell Pi OS a user exists (skips first-boot wizard)
if [ -d /boot/firmware ]; then
    HASH=$(echo 'umbgs' | openssl passwd -6 -stdin)
    echo "umbgs:${HASH}" > /boot/firmware/userconf.txt
fi

# Disable Pi OS first-boot user-creation wizard
systemctl disable userconfig 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/userconfig.service

# Auto-login on tty1 (touchscreen console)
systemctl enable getty@tty1.service
mkdir -p /etc/systemd/system/getty@tty1.service.d
config_file "getty-autologin.conf" /etc/systemd/system/getty@tty1.service.d/autologin.conf

# ─── SSH ──────────────────────────────────────────────────────────
log "Enabling SSH..."
systemctl enable ssh
[ -d /boot/firmware ] && touch /boot/firmware/ssh

# ─── WiFi regulatory domain ──────────────────────────────────────
log "Setting WiFi regulatory domain..."
echo 'REGDOMAIN=US' > /etc/default/crda

# ─── Boot firmware config ────────────────────────────────────────
if [ -d /boot/firmware ]; then
    log "Configuring boot firmware..."

    # hdmi_force_hotplug=1 keeps HDMI active even when the 7" panel isn't "hot"
    # at power-on — otherwise Plymouth renders before the display is live and
    # the splash never shows.
    for param in \
        "dtoverlay=disable-bt" \
        "dtparam=audio=on" \
        "disable_splash=1" \
        "auto_initramfs=1" \
        "hdmi_force_hotplug=1"; do
        grep -qxF "$param" /boot/firmware/config.txt 2>/dev/null || echo "$param" >> /boot/firmware/config.txt
    done

    # The MPI7002 7" touchscreen's EDID lies about supported modes (advertises
    # 1920x1080) and its internal scaler refuses custom modes, so we render at
    # the reported 1920x1080 and compensate with CSS zoom in the dashboard.
    if ! grep -q "cfg80211.ieee80211_regdom" /boot/firmware/cmdline.txt 2>/dev/null; then
        sed -i '/^console=/ s/$/ splash logo.nologo vt.global_cursor_default=0 cfg80211.ieee80211_regdom=US/' /boot/firmware/cmdline.txt
    fi
fi

# ─── Blank cursor theme for kiosk ────────────────────────────────
# cage/wlroots always draws a pointer when an input device with pointer
# capability is present (the touchscreen reports as one). There's no flag to
# disable cursor rendering, so we ship a transparent xcursor theme. Both
# XCURSOR_THEME=blank (set on the cage process env) AND the system-default
# /usr/share/icons/default theme are needed — env var alone doesn't work
# (libxcursor seems to fall back to the system default in some code paths).
# The cursor must be 24x24: libxcursor rejects 1x1 cursors as invalid and
# falls through to another theme.
if [ ! -f /usr/share/icons/blank/cursors/left_ptr ]; then
    log "Installing blank cursor theme..."
    mkdir -p /usr/share/icons/blank/cursors /usr/share/icons/default
    cat > /usr/share/icons/blank/index.theme <<'EOF'
[Icon Theme]
Name=blank
Comment=Invisible cursor theme for kiosk use
Inherits=core
EOF
    cat > /usr/share/icons/blank/cursor.theme <<'EOF'
[Icon Theme]
Name=blank
Inherits=core
EOF
    # Make the system default cursor theme inherit from blank so ALL cursor
    # lookups fall through to transparent, regardless of what theme a client
    # requests.
    cat > /usr/share/icons/default/index.theme <<'EOF'
[Icon Theme]
Name=Default
Comment=Default cursor theme (transparent/blank)
Inherits=blank
EOF
    # Generate a 24x24 fully-transparent xcursor file and symlink all common
    # cursor names to it. Format documented at:
    # https://www.freedesktop.org/wiki/Specifications/cursor-spec/
    python3 - <<'PYEOF'
import os, struct
W, H = 24, 24
magic = b'Xcur'
data  = struct.pack('<4sIII', magic, 16, 0x00010000, 1)                 # file header
data += struct.pack('<III', 0xfffd0002, W, 28)                           # TOC: type=image, nominal_size=W, offset=28
data += struct.pack('<IIIIIIIII', 36, 0xfffd0002, W, 1, W, H, 0, 0, 0)   # image header
data += b'\x00' * (W * H * 4)                                            # all-transparent ARGB pixels
target = '/usr/share/icons/blank/cursors/left_ptr'
with open(target, 'wb') as f:
    f.write(data)
# Symlink every cursor shape cage/cog might ask for
names = ['default', 'arrow', 'top_left_arrow', 'left_ptr_watch', 'xterm',
         'text', 'hand', 'hand1', 'hand2', 'pointer', 'crosshair', 'wait',
         'progress', 'help', 'not-allowed', 'grab', 'grabbing',
         'sb_h_double_arrow', 'sb_v_double_arrow', 'fleur', 'move']
for name in names:
    link = f'/usr/share/icons/blank/cursors/{name}'
    if not os.path.exists(link):
        os.symlink('left_ptr', link)
PYEOF
fi

# ─── Plymouth splash screen ──────────────────────────────────────
log "Setting up Plymouth splash..."
mkdir -p /usr/share/plymouth/themes/pix
fetch_file "assets/splash.png" /usr/share/plymouth/themes/pix/splash.png
config_file "pix.plymouth"     /usr/share/plymouth/themes/pix/pix.plymouth
config_file "pix.script"       /usr/share/plymouth/themes/pix/pix.script
if $IN_CHROOT; then
    # update-initramfs often fails under QEMU chroot; set theme now, rebuild on first boot
    plymouth-set-default-theme pix
    log "Plymouth theme set (initramfs rebuild deferred to first boot)"
else
    plymouth-set-default-theme -R pix
fi

# ─── Done ─────────────────────────────────────────────────────────
log "=== Installation complete ==="

if ! $IN_CHROOT; then
    log "Running on real hardware — executing first-boot setup now..."
    /usr/local/bin/umbgs-firstboot.sh || true
    log ""
    log "Ground station installed. Next steps:"
    log "  1. Edit /boot/firmware/ground-station.yaml with your callsign and WiFi"
    log "  2. Reboot: sudo reboot"
    log "  3. Dashboard will be at http://<ip>:8080"
else
    log "Packer/chroot build complete. First-boot setup will run on initial boot."
fi
