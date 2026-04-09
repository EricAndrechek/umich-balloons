#!/bin/bash
# umbgs-watchdog.sh - A/B slot watchdog for ground station binary.
# Runs via systemd timer every 5 minutes. Rolls back if pending update is stale.
# This script is intentionally simple and NEVER auto-updated.

set -euo pipefail

ACTIVE_FILE="/data/active"
PENDING_FILE="/data/pending"
SLOT_A="/data/umbgs-a"
SLOT_B="/data/umbgs-b"
SYMLINK="/data/umbgs"
MAX_PENDING_AGE=600  # 10 minutes - if pending file is older, rollback

# If no pending file, nothing to do
if [ ! -f "$PENDING_FILE" ]; then
    exit 0
fi

# Check age of pending file
pending_age=$(( $(date +%s) - $(stat -c %Y "$PENDING_FILE" 2>/dev/null || echo 0) ))

if [ "$pending_age" -lt "$MAX_PENDING_AGE" ]; then
    # Still within grace period, let the new version prove itself
    exit 0
fi

echo "WATCHDOG: Pending file is ${pending_age}s old (max ${MAX_PENDING_AGE}s). Rolling back."

# Read current active slot
active=$(cat "$ACTIVE_FILE" 2>/dev/null || echo "a")

# Roll back to the other slot
if [ "$active" = "b" ]; then
    rollback="a"
else
    rollback="b"
fi

# Verify rollback target exists
rollback_path="/data/umbgs-${rollback}"
if [ ! -x "$rollback_path" ]; then
    echo "WATCHDOG: Rollback target $rollback_path not found or not executable!"
    # Remove pending to stop further watchdog runs
    rm -f "$PENDING_FILE"
    exit 1
fi

# Switch active slot
echo "$rollback" > "$ACTIVE_FILE"
rm -f "$SYMLINK"
ln -s "$rollback_path" "$SYMLINK"
rm -f "$PENDING_FILE"

echo "WATCHDOG: Rolled back to slot $rollback. Restarting umbgs."
systemctl restart umbgs.service
