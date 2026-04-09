#!/usr/bin/env python3
"""
Reports ground station GPS position to the API as a SondeHub chase vehicle.
Reads position from gpsd and POSTs to /station every REPORT_INTERVAL seconds.

Requires: gpsd running, gpsd-clients installed
Install: sudo apt install gpsd gpsd-clients python3-gps
"""

import json
import logging
import socket
import sys
import time

import requests
from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE

# --- Configuration ---
API_ENDPOINT = "https://api.umich-balloons.com/station"
API_HEADERS = {"Content-Type": "application/json"}
REPORT_INTERVAL = 30  # seconds between position reports
GPSD_HOST = "127.0.0.1"
GPSD_PORT = 2947
LOG_FILE = "/var/log/gps_reporter.log"
# --- End Configuration ---

# --- Logging Setup ---
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if LOG_FILE:
    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        print(f"ERROR: Could not write to log file {LOG_FILE}.", file=sys.stderr)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# --- Get Hostname ---
try:
    PI_HOSTNAME = socket.gethostname()
except Exception:
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


def get_gps_position(session):
    """Read from gpsd until we get a fix with lat/lon/alt."""
    deadline = time.time() + 10  # 10s timeout for getting a fix
    while time.time() < deadline:
        report = session.next()
        if report["class"] == "TPV":
            lat = getattr(report, "lat", None)
            lon = getattr(report, "lon", None)
            alt = getattr(report, "altHAE", getattr(report, "alt", None))
            if lat is not None and lon is not None:
                return lat, lon, alt if alt is not None else 0.0
    return None


def main():
    logger.info(f"Starting GPS reporter for {STATION_CALLSIGN}")
    logger.info(f"Reporting to {API_ENDPOINT} every {REPORT_INTERVAL}s")

    while True:
        session = None
        try:
            session = gps(host=GPSD_HOST, port=GPSD_PORT, mode=WATCH_ENABLE | WATCH_NEWSTYLE)
            logger.info("Connected to gpsd")

            while True:
                position = get_gps_position(session)
                if position is None:
                    logger.warning("No GPS fix available")
                    time.sleep(REPORT_INTERVAL)
                    continue

                lat, lon, alt = position
                payload = {
                    "callsign": STATION_CALLSIGN,
                    "lat": lat,
                    "lon": lon,
                    "alt": alt,
                }

                try:
                    resp = requests.post(
                        API_ENDPOINT,
                        headers=API_HEADERS,
                        data=json.dumps(payload),
                        timeout=10,
                    )
                    if 200 <= resp.status_code < 300:
                        logger.info(f"Position reported: {lat:.6f}, {lon:.6f}, {alt:.1f}m")
                    else:
                        logger.warning(f"API returned {resp.status_code}: {resp.text}")
                except requests.exceptions.RequestException as e:
                    logger.error(f"API error: {e}")

                time.sleep(REPORT_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break
        except Exception as e:
            logger.error(f"Error: {e}. Reconnecting in 15s...")
            time.sleep(15)
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
