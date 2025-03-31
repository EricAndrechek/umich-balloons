#!/bin/sh
# mosquitto/entrypoint.sh - MODIFIED FOR TWO USERS

set -e

# --- Configuration ---
BRIDGE_USERNAME="${MQTT_BRIDGE_USERNAME}"
BRIDGE_PASSWORD="${MQTT_BRIDGE_PASSWORD}"

CONFIG_FILE="/mosquitto/config/mosquitto.conf"

# --- Validate Input ---
if [ -z "${BRIDGE_USERNAME}" ] || [ -z "${BRIDGE_PASSWORD}" ]; then
  echo "Error: MQTT_BRIDGE_USER and MQTT_BRIDGE_PASSWORD environment variables must be set." >&2
  exit 1
fi

# --- Modify *.js scripts to include the new user ---
echo "Modifying *.js scripts to include the new user..."
# look for all *.js files in the /mosquitto/config directory
# and replace:
# bridge_username = "bridge_username";
# bridge_password = "bridge_password";
# with the username and password from the environment variables
for file in /mosquitto/config/*.js; do
  if [ -f "$file" ]; then
    sed -i "s/bridge_username = \"bridge_username\";/bridge_username = \"${BRIDGE_USERNAME}\";/g" "$file"
    sed -i "s/bridge_password = \"bridge_password\";/bridge_password = \"${BRIDGE_PASSWORD}\";/g" "$file"
  fi
done

# --- Start Mosquitto ---
echo "Starting Mosquitto..."
exec mosquitto -c "${CONFIG_FILE}"