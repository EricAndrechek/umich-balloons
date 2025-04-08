#!/usr/bin/env python3

from re import S
import serial
import requests
import json
import time
import logging
import sys
from datetime import datetime, timezone
import socket
import os

# --- Configuration ---
SERIAL_PORT = '/dev/ttyACM0'  # Or '/dev/ttyUSB0', check your connected Arduino
BAUD_RATE = 9600
API_ENDPOINT = 'https://api.umich-balloons.com/lora' # CHANGE THIS to your actual API endpoint URL
API_HEADERS = {'Content-Type': 'application/json'}
# Optional: Add authentication headers if needed
# API_HEADERS['Authorization'] = 'Bearer YOUR_API_KEY'

RETRY_DELAY_SERIAL = 10 # Seconds to wait before retrying serial connection
RETRY_DELAY_API = 5    # Seconds to wait before retrying API post after failure
LOG_FILE = '/var/log/serial_to_api.log' # Optional: Set to None to log to console/journald only
# --- End Configuration ---

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Set to logging.DEBUG for more verbose output

# Log to file if specified
if LOG_FILE:
    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        print(f"ERROR: Could not write to log file {LOG_FILE}. Check permissions.", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Could not set up log file {LOG_FILE}: {e}", file=sys.stderr)


# Also log to standard output (which systemd can capture)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# --- Get Hostname ---
try:
    PI_HOSTNAME = socket.gethostname() # <-- Get the hostname here
except Exception as e:
    print(f"WARNING: Could not get hostname: {e}", file=sys.stderr)
    PI_HOSTNAME = "unknown_hostname" # Fallback hostname
# --- End Configuration ---

# --- Main Logic ---
def main():
    logger.info("Starting serial_to_api script...")
    ser = None # Initialize serial connection variable

    while True:
        try:
            # --- Connect to Serial Port ---
            if ser is None or not ser.is_open:
                # serial port could change, look for which one is Arduino or a CH340 serial converter
                # --- Check for Serial Port ---
                # run lsusb to find the correct port
                lsusb_output = os.popen('lsusb').read().strip()
                if 'Arduino' in lsusb_output or 'CH340' in lsusb_output or 'USB-Serial' in lsusb_output or 'FTDI' in lsusb_output:
                    logger.info("Potential Arduino detected. Proceeding with serial connection.")
                    # check which port that was on
                    for line in lsusb_output.splitlines():
                        if 'Arduino' in line or 'CH340' in line or 'USB-Serial' in line or 'FTDI' in line:
                            # Extract the port name from the line
                            # Example: Bus 001 Device 005: ID 2341:0043 Arduino SA Uno WiFi Rev2
                            # we can use /dev/bus/usb/<bus>/<device> to find the port
                            line_by_spaces = line.split()
                            # 0: Bus
                            bus = line_by_spaces[1]
                            # 2: Device
                            device = line_by_spaces[3].split(':')[0]
                            # 4: ID
                            id = line_by_spaces[5]

                            id_1 = id.split(':')[0] # Get the first part of the ID

                            logger.info(f"Potential Arduino/CH340 detected: {line}")
                            logger.info(f"Bus: {bus}, Device: {device}, ID: {id}")

                            # read the files available in /dev/serial/by-id/
                            id_usbs = os.popen('ls /dev/serial/by-id/').read().strip()
                            for id_usb in id_usbs.split():
                                logger.info(f"Checking {id_usb} for Arduino")
                                # for CH340 only the first part is in the path?
                                if id_1 in id_usb:
                                    # found the port
                                    SERIAL_PORT = os.path.join('/dev/serial/by-id/', id_usb)
                                    logger.info(f"Found Arduino/CH340 on {SERIAL_PORT}")
                                    break
                            else:
                                logger.warning("No Arduino or CH340 detected. Check your connections.")
                                raise Exception("No Arduino or CH340 detected.")
                else:
                    logger.warning(f"No Arduino or CH340 detected. Check your connections. All I can see is: {lsusb_output}")
                    raise Exception("No Arduino or CH340 detected.")

                logger.info(f"Attempting to connect to serial port {SERIAL_PORT} at {BAUD_RATE} baud.")
                try:
                    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                    logger.info("Serial port connected successfully.")
                    # Allow time for connection to establish and Arduino to reset
                    time.sleep(2)
                except serial.SerialException as e:
                    logger.error(f"Serial Error: {e}. Retrying in {RETRY_DELAY_SERIAL} seconds...")
                    ser = None # Ensure ser is None if connection failed
                    time.sleep(RETRY_DELAY_SERIAL)
                    continue # Go back to the start of the while loop
                except Exception as e:
                    logger.error(f"Unexpected error opening serial port: {e}. Retrying in {RETRY_DELAY_SERIAL} seconds...")
                    ser = None
                    time.sleep(RETRY_DELAY_SERIAL)
                    continue

            # --- Read from Serial ---
            if ser.in_waiting > 0:
                try:
                    # Read a line, decode from bytes to string, remove leading/trailing whitespace
                    line = ser.readline().decode('utf-8', errors='ignore').strip()

                    if line: # Proceed only if the line is not empty
                        logger.debug(f"Read from serial: {line}")

                        if line.startswith('[DEBUG]'):
                            # Skip debug lines
                            logger.debug(f"Skipping debug line: {line}")
                            continue
                        else:
                            logger.info(f"Processing line: {line}")

                            # --- Prepare JSON Data ---
                            # Customize this dictionary based on your data and API requirements
                            payload = {
                                'sender': PI_HOSTNAME,
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                                'raw_data': line
                            }

                            json_payload = json.dumps(payload)
                            logger.debug(f"Sending JSON: {json_payload}")

                            # --- Send Data to API ---
                            try:
                                response = requests.post(API_ENDPOINT, headers=API_HEADERS, data=json_payload, timeout=10) # 10 second timeout

                                if 200 <= response.status_code < 300:
                                    logger.info(f"Data sent successfully. Status: {response.status_code}")
                                else:
                                    logger.warning(f"API returned non-success status: {response.status_code} - {response.text}")
                                    # Optional: Add specific handling for certain error codes (e.g., 4xx, 5xx)

                            except requests.exceptions.RequestException as api_err:
                                logger.error(f"API Error: {api_err}. Retrying in {RETRY_DELAY_API} seconds...")
                                time.sleep(RETRY_DELAY_API)
                                # No 'continue' here, we might still be able to read next serial line
                            except Exception as e:
                                logger.error(f"Unexpected error during API call: {e}")
                                time.sleep(RETRY_DELAY_API)

                    # Small delay to prevent tight loop hammering CPU if no data is coming
                    else:
                        time.sleep(0.1)

                except serial.SerialException as e:
                    logger.error(f"Serial error during read: {e}. Attempting to reconnect...")
                    if ser and ser.is_open:
                        ser.close()
                    ser = None # Mark serial as disconnected
                    time.sleep(RETRY_DELAY_SERIAL) # Wait before attempting reconnect in the outer loop
                except UnicodeDecodeError as e:
                    logger.warning(f"Error decoding serial data: {e}. Skipping line.")
                except Exception as e:
                    logger.error(f"Unexpected error in main loop: {e}")
                    # Decide if you want to try reconnecting serial or just wait
                    time.sleep(5) # General wait on unexpected error

            else:
                # No data waiting, sleep briefly to avoid high CPU usage
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Shutdown requested by user.")
            break
        except Exception as e:
            # Catch-all for unexpected issues in the outer loop logic (e.g., config errors)
            logger.error(f"Critical error in outer loop: {e}. Restarting loop after delay...")
            time.sleep(RETRY_DELAY_SERIAL) # Wait before restarting the whole process

    # --- Cleanup ---
    if ser and ser.is_open:
        ser.close()
        logger.info("Serial port closed.")
    logger.info("serial_to_api script finished.")

if __name__ == "__main__":
    main()
