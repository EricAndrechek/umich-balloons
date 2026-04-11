#!/bin/bash
# firstboot.sh - UMB Ground Station first-boot setup
# Runs once on real hardware (not in chroot/QEMU).
# Handles tasks that require running services or real hardware access.
set -e

log() { echo "[umbgs-firstboot] $*"; }

log "=== First boot setup ==="

# ─── Set hostname ─────────────────────────────────────────────────
CURRENT=$(hostname)
if [ "$CURRENT" = "raspberrypi" ] || [ "$CURRENT" = "localhost" ]; then
    log "Setting hostname to umb-ground-station..."
    hostnamectl set-hostname umb-ground-station 2>/dev/null || true
fi

# ─── Rebuild initramfs (deferred from Packer chroot for Plymouth splash) ──
if [ -f /usr/share/plymouth/themes/pix/splash.png ]; then
    log "Rebuilding initramfs for Plymouth splash..."
    update-initramfs -u 2>/dev/null || true
fi

# ─── Mark first boot complete ─────────────────────────────────────
mkdir -p /data
touch /data/.firstboot-done

log "=== First boot setup complete ==="
