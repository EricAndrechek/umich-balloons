#!/bin/sh
# mosquitto/entrypoint.sh - MODIFIED FOR TWO USERS

set -e

# --- Configuration ---
PI_USERNAME="${MQTT_USERNAME}"
PI_PASSWORD="${MQTT_PASSWORD}"
BRIDGE_USERNAME="${MQTT_BRIDGE_USER}"
BRIDGE_PASSWORD="${MQTT_BRIDGE_PASSWORD}"
PASSWORD_FILE="/mosquitto/config/passwd"
CONFIG_FILE="/mosquitto/config/mosquitto.conf"

# --- Validate Input ---
if [ -z "${PI_USERNAME}" ] || [ -z "${PI_PASSWORD}" ] || \
   [ -z "${BRIDGE_USERNAME}" ] || [ -z "${BRIDGE_PASSWORD}" ]; then
  echo "Error: MQTT_USERNAME, MQTT_PASSWORD, MQTT_BRIDGE_USER, and MQTT_BRIDGE_PASSWORD environment variables must be set." >&2
  exit 1
fi

# --- Create/Update Password File ---
echo "Setting up MQTT users..."
mkdir -p "$(dirname "${PASSWORD_FILE}")"

# Create file with the FIRST user (-c flag)
if ! mosquitto_passwd -b -c "${PASSWORD_FILE}" "${PI_USERNAME}" "${PI_PASSWORD}"; then
    echo "Error: Failed initial mosquitto_passwd for PI user." >&2; exit 1;
fi
echo "Added/Updated PI user '${PI_USERNAME}'."

# Add the SECOND user (no -c flag)
if ! mosquitto_passwd -b "${PASSWORD_FILE}" "${BRIDGE_USERNAME}" "${BRIDGE_PASSWORD}"; then
    echo "Error: Failed adding BRIDGE user with mosquitto_passwd." >&2; exit 1;
fi
echo "Added/Updated BRIDGE user '${BRIDGE_USERNAME}'."


# Set permissions
chown mosquitto:mosquitto "${PASSWORD_FILE}" || echo "Warning: Could not chown password file."
chmod 600 "${PASSWORD_FILE}"
echo "Password file permissions set."

# --- Start Mosquitto ---
echo "Starting Mosquitto..."
exec mosquitto -c "${CONFIG_FILE}"