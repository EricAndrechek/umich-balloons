#!/bin/bash
# firstboot.sh - UMB Ground Station first-boot setup
# Runs once on real hardware (not in chroot/QEMU).
# Handles tasks that require running services or real hardware access.
set -e

log() { echo "[umbgs-firstboot] $*"; }

log "=== First boot setup ==="

# ─── Unblock WiFi radio (hardware-agnostic, works on Pi 4 + Pi 5) ──
log "Unblocking WiFi radio..."
rfkill unblock wifi 2>/dev/null || true

# ─── Set hostname ─────────────────────────────────────────────────
CURRENT=$(hostname)
if [ "$CURRENT" = "raspberrypi" ] || [ "$CURRENT" = "localhost" ]; then
    log "Setting hostname to umb-ground-station..."
    hostnamectl set-hostname umb-ground-station 2>/dev/null || true
fi

# ─── Mark first boot complete ─────────────────────────────────────
mkdir -p /data
touch /data/.firstboot-done

log "=== First boot setup complete ==="
