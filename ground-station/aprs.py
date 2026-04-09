#!/usr/bin/env python3

import socket
import requests
import json
import time
import logging
import sys
from datetime import datetime, timezone

# --- Configuration ---
DIREWOLF_HOST = '127.0.0.1'  # Direwolf host (usually localhost)
DIREWOLF_PORT = 8001        # Port configured in direwolf.conf (KISSPORT or AGWPEPORT)
API_ENDPOINT = 'https://api.umich-balloons.com/aprs' # CHANGE THIS - Same API endpoint
API_HEADERS = {'Content-Type': 'application/json'}
# Optional: Add authentication headers if needed
# API_HEADERS['Authorization'] = 'Bearer YOUR_API_KEY'

RETRY_DELAY_DIREWOLF = 15 # Seconds to wait before retrying Direwolf connection
RETRY_DELAY_API = 5      # Seconds to wait before retrying API post after failure
LOG_FILE = '/var/log/direwolf_to_api.log' # Optional: Set to None to log to console/journald only

# Common Direwolf status prefixes/lines to ignore (adjust as needed)
IGNORE_PREFIXES = (
    "[", "Audio input level", "***", "Digipeater", "Dire Wolf",
    "Copyright", "Ready", "Channel", "Current TNC", "Sending",
    "Valid", "Fixed", "Unknown", "WARNING", "Too", "Position",
    "Rate", "Received", "Loading", "Set up", "Note:", "AGWPE",
    "KISS"
)

# --- Get Hostname ---
try:
    PI_HOSTNAME = socket.gethostname()
except Exception as e:
    print(f"WARNING: Could not get hostname: {e}", file=sys.stderr)
    PI_HOSTNAME = "unknown_hostname"

# --- Get Callsign from config (fallback to hostname) ---
STATION_CALLSIGN = PI_HOSTNAME
try:
    with open('/etc/ground-station.conf', 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('CALLSIGN='):
                val = line.split('=', 1)[1].strip().strip('"').strip("'")
                if val:
                    STATION_CALLSIGN = val
                    break
except FileNotFoundError:
    pass
except Exception as e:
    print(f"WARNING: Could not read /etc/ground-station.conf: {e}", file=sys.stderr)
# --- End Configuration ---

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(hostname)s - %(message)s') # Added hostname to log format
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Set to logging.DEBUG for more verbose output

# Add hostname context to logger
adapter = logging.LoggerAdapter(logger, {'hostname': STATION_CALLSIGN})

# Log to file if specified
if LOG_FILE:
    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        adapter.error(f"Could not write to log file {LOG_FILE}. Check permissions.")
    except Exception as e:
        adapter.error(f"Could not set up log file {LOG_FILE}: {e}")

# Also log to standard output (which systemd can capture)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# --- Main Logic ---
def main():
    adapter.info(f"Starting direwolf_to_api script. Connecting to {DIREWOLF_HOST}:{DIREWOLF_PORT}")
    sock = None

    while True:
        try:
            # --- Connect to Direwolf ---
            if sock is None:
                adapter.info(f"Attempting to connect to Direwolf at {DIREWOLF_HOST}:{DIREWOLF_PORT}")
                try:
                    # Create socket and connect
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    # Set a timeout for the connection attempt itself
                    sock.settimeout(10)
                    sock.connect((DIREWOLF_HOST, DIREWOLF_PORT))
                    # Set a longer timeout for read operations or make it blocking
                    sock.settimeout(60) # e.g., 60 seconds timeout for reads
                    # sock.setblocking(True) # Alternative: make reads block indefinitely
                    adapter.info("Connected to Direwolf successfully.")
                    # Create a file-like object for easy line reading
                    sock_file = sock.makefile('r', encoding='utf-8', errors='ignore')

                except socket.timeout:
                    adapter.error("Connection to Direwolf timed out.")
                    if sock: sock.close()
                    sock = None
                    sock_file = None
                    adapter.info(f"Retrying connection in {RETRY_DELAY_DIREWOLF} seconds...")
                    time.sleep(RETRY_DELAY_DIREWOLF)
                    continue
                except socket.error as e:
                    adapter.error(f"Socket Error connecting to Direwolf: {e}")
                    if sock: sock.close()
                    sock = None
                    sock_file = None
                    adapter.info(f"Retrying connection in {RETRY_DELAY_DIREWOLF} seconds...")
                    time.sleep(RETRY_DELAY_DIREWOLF)
                    continue
                except Exception as e:
                    adapter.error(f"Unexpected error connecting to Direwolf: {e}")
                    if sock: sock.close()
                    sock = None
                    sock_file = None
                    adapter.info(f"Retrying connection in {RETRY_DELAY_DIREWOLF} seconds...")
                    time.sleep(RETRY_DELAY_DIREWOLF)
                    continue

            # --- Read from Direwolf Socket ---
            try:
                line = sock_file.readline()
                if not line:
                    # Empty string means Direwolf closed the connection
                    adapter.warning("Direwolf closed the connection. Attempting to reconnect...")
                    sock_file.close()
                    sock.close()
                    sock = None
                    sock_file = None
                    time.sleep(RETRY_DELAY_DIREWOLF / 2) # Wait a bit before hammering reconnect
                    continue

                line = line.strip() # Remove leading/trailing whitespace

                if line: # Proceed only if the line is not empty
                    adapter.debug(f"Received from Direwolf: {line}")

                    # --- Filter for APRS Data ---
                    # Basic check: Does it contain '>' and not start with common noise?
                    is_noise = any(line.startswith(prefix) for prefix in IGNORE_PREFIXES)
                    is_likely_aprs = '>' in line and not is_noise

                    if is_likely_aprs:
                        adapter.info(f"Detected APRS packet: {line}")

                        # --- Prepare JSON Data ---
                        payload = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "sender": STATION_CALLSIGN,
                            "raw_data": line,
                        }

                        json_payload = json.dumps(payload)
                        adapter.debug(f"Sending JSON: {json_payload}")

                        # --- Send Data to API ---
                        try:
                            response = requests.post(API_ENDPOINT, headers=API_HEADERS, data=json_payload, timeout=10)

                            if 200 <= response.status_code < 300:
                                adapter.info(f"APRS data sent successfully. Status: {response.status_code}")
                            else:
                                adapter.warning(f"API returned non-success status: {response.status_code} - {response.text}")
                                # Optional: Consider if retrying the *same* packet is useful
                                # time.sleep(RETRY_DELAY_API)

                        except requests.exceptions.RequestException as api_err:
                            adapter.error(f"API Error sending APRS data: {api_err}.")
                            # Don't sleep here, process next line from Direwolf. Could lose packets if API is down long term.
                            # If guaranteed delivery is needed, implement a queue.
                        except Exception as e:
                            adapter.error(f"Unexpected error during API call: {e}")

                    else:
                        adapter.debug(f"Ignoring non-APRS line: {line}")

            except socket.timeout:
                adapter.debug("Socket read timed out. No data from Direwolf.")
                # This is normal if there's no traffic. Just loop again.
                continue
            except socket.error as e:
                adapter.error(f"Socket error during read: {e}. Attempting to reconnect...")
                if sock_file: sock_file.close()
                if sock: sock.close()
                sock = None
                sock_file = None
                time.sleep(RETRY_DELAY_DIREWOLF / 2)
                continue # Go back to the start of the while loop to reconnect
            except UnicodeDecodeError as e:
                adapter.warning(f"Error decoding Direwolf data: {e}. Skipping line.")
            except Exception as e:
                adapter.error(f"Unexpected error in read loop: {e}. Attempting to reconnect...")
                if sock_file: sock_file.close()
                if sock: sock.close()
                sock = None
                sock_file = None
                time.sleep(RETRY_DELAY_DIREWOLF / 2)
                continue

        except KeyboardInterrupt:
            adapter.info("Shutdown requested by user.")
            break
        except Exception as e:
            # Catch-all for unexpected issues in the outer loop logic
            adapter.error(f"Critical error in outer loop: {e}. Restarting loop after delay...")
            if sock_file: sock_file.close()
            if sock: sock.close() # Ensure socket is closed before delay
            sock = None
            sock_file = None
            time.sleep(RETRY_DELAY_DIREWOLF) # Wait before restarting the whole process

    # --- Cleanup ---
    adapter.info("Closing resources...")
    if sock_file:
        sock_file.close()
    if sock and sock.fileno() != -1: # Check if socket is valid before closing
        try:
            sock.shutdown(socket.SHUT_RDWR) # Gracefully shutdown
        except socket.error:
            pass # Ignore errors if socket is already closed
        finally:
            sock.close()

    adapter.info("direwolf_to_api script finished.")

if __name__ == "__main__":
    main()
