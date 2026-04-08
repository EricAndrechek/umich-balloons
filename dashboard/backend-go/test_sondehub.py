# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "aprslib"]
# ///
"""
End-to-end test for umich-balloons Go relay and APRS-IS -> SondeHub pipeline.

Simulates a balloon (KF8ABL-12) drifting NE from Ann Arbor, ascending slowly.
Sends telemetry via four paths, staggered evenly across each tick:
  1. APRS-IS  – raw packets injected into the real APRS-IS network
  2. APRS push – POST /aprs on the Go relay (15s later)
  3. LoRa push – POST /lora on the Go relay (30s later)
  4. Iridium   – POST /iridium on the Go relay, JWT skipped in dev mode (45s later)

Automatically resumes from the last test position if the previous run ended
less than 30 minutes ago (saved in .test_state.json). Use --fresh to force
starting from scratch.

Usage:
  uv run test_sondehub.py [--relay http://localhost:8080] [--no-aprs-is] [--fresh]

Prerequisites:
  - Go relay running (DEV_MODE=true skips Iridium JWT check)
  - A valid APRS-IS passcode for your callsign (KD8CJT-9)
"""

import argparse
import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

BALLOON_CALLSIGN = "KF8ABL-12"
APRS_IS_LOGIN_CALL = "KD8CJT-9"
APRS_IS_PASSCODE = "19121"
IRIDIUM_IMEI = "301434060500780"

# Start near Ann Arbor, MI
START_LAT = 42.2808
START_LON = -83.7430
START_ALT = 300.0  # meters

# Movement per tick
DRIFT_LAT = 0.002   # ~220m north per minute
DRIFT_LON = 0.003   # ~250m east per minute
CLIMB_RATE = 150.0   # meters per minute

STATE_FILE = Path(__file__).parent / ".test_state.json"
RESUME_MAX_AGE_S = 1800  # 30 minutes

TICK_SECONDS = 60
NUM_TICKS = 15  # 15 minutes of flight

SONDEHUB_TRACKER_URL = "https://amateur.sondehub.org"

# ── Helpers ─────────────────────────────────────────────────────────────────

def wait_until_second(target_sec: int):
    """Sleep until the next occurrence of `target_sec` within the current minute.
    target_sec should be 0, 15, 30, or 45."""
    now = time.time()
    current_sec = now % 60
    wait = (target_sec - current_sec) % 60
    if wait < 0.05:  # already there (within 50ms)
        wait = 0
    if wait > 0:
        time.sleep(wait)
    return datetime.now(timezone.utc)

# ── State persistence ──────────────────────────────────────────────────────

def save_state(lat: float, lon: float, alt: float, tick: int):
    """Save current position to disk so the next run can resume."""
    state = {
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "tick": tick,
        "timestamp": time.time(),
        "callsign": BALLOON_CALLSIGN,
    }
    STATE_FILE.write_text(json.dumps(state))


def load_state():
    """Load previous state if it exists and is recent enough."""
    if not STATE_FILE.exists():
        return None
    try:
        state = json.loads(STATE_FILE.read_text())
        age = time.time() - state["timestamp"]
        if age > RESUME_MAX_AGE_S:
            return None
        if state.get("callsign") != BALLOON_CALLSIGN:
            return None
        return state
    except (json.JSONDecodeError, KeyError):
        return None

# ── APRS-IS Connection ─────────────────────────────────────────────────────

class APRSISConnection:
    """Minimal APRS-IS connection for injecting packets."""

    def __init__(self, callsign: str, passcode: str, server: str = "rotate.aprs2.net", port: int = 14580):
        self.callsign = callsign
        self.passcode = passcode
        self.server = server
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.server, self.port), timeout=15)
        self.sock.settimeout(10)
        # Read banner
        banner = self.sock.recv(512).decode("ascii", errors="replace")
        print(f"  APRS-IS banner: {banner.strip()}")
        # Login
        login = f"user {self.callsign} pass {self.passcode} vers umich-balloons-test 1.0\r\n"
        self.sock.sendall(login.encode("ascii"))
        resp = self.sock.recv(512).decode("ascii", errors="replace")
        if "logresp" not in resp.lower():
            raise ConnectionError(f"APRS-IS login failed: {resp.strip()}")
        verified = "verified" in resp.lower() and "unverified" not in resp.lower()
        print(f"  APRS-IS login: {resp.strip()} (verified={verified})")
        if not verified:
            print("  WARNING: unverified – packets will NOT gate to SondeHub via APRS-IS")

    def send_packet(self, packet: str):
        if self.sock is None:
            raise ConnectionError("Not connected")
        self.sock.sendall((packet + "\r\n").encode("ascii"))

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None


def build_aprs_packet(callsign: str, lat: float, lon: float, alt_m: float,
                      course: int = 45, speed_knots: int = 5) -> str:
    """Build an uncompressed APRS position packet with balloon symbol 'O'."""
    # Latitude: DDMM.hhN
    lat_dir = "N" if lat >= 0 else "S"
    lat = abs(lat)
    lat_deg = int(lat)
    lat_min = (lat - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:05.2f}{lat_dir}"

    # Longitude: DDDMM.hhW
    lon_dir = "E" if lon >= 0 else "W"
    lon = abs(lon)
    lon_deg = int(lon)
    lon_min = (lon - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:05.2f}{lon_dir}"

    alt_feet = int(alt_m * 3.28084)

    # Symbol: /O = balloon
    # Format: !DDMM.hhN/DDDMM.hhWOCSE/SPD/A=NNNNNN
    info = f"!{lat_str}/{lon_str}O{course:03d}/{speed_knots:03d}/A={alt_feet:06d}"
    return f"{callsign}>APRS,TCPIP*:{info}"


# ── Relay Senders ───────────────────────────────────────────────────────────

def send_aprs_relay(relay_url: str, callsign: str, lat: float, lon: float,
                    alt_m: float, course: int, speed_knots: int, ts: datetime):
    """POST /aprs to Go relay."""
    packet = build_aprs_packet(callsign, lat, lon, alt_m, course, speed_knots)
    resp = requests.post(f"{relay_url}/aprs", json={
        "sender": "test-script",
        "raw_data": packet,
        "timestamp": ts.isoformat(),
    }, timeout=5)
    return resp.status_code, resp.text.strip()


def send_lora_relay(relay_url: str, callsign: str, lat: float, lon: float,
                    alt_m: float, course: int, speed_ms: float, batt_mv: int,
                    sats: int, temp: float, ts: datetime):
    """POST /lora to Go relay."""
    resp = requests.post(f"{relay_url}/lora", json={
        "sender": "test-ground-station",
        "raw_data": {
            "callsign": callsign,
            "lat": lat,
            "lon": lon,
            "alt": alt_m,
            "heading": course,
            "speed": speed_ms,
            "battery": batt_mv,
            "sats": sats,
            "temp": temp,
        },
        "timestamp": ts.isoformat(),
    }, timeout=5)
    return resp.status_code, resp.text.strip()


def send_iridium_relay(relay_url: str, imei: str, callsign: str,
                       lat: float, lon: float, alt_m: float,
                       course: int, speed_ms: float,
                       batt_v_x10: int, uptime_s: int, ts: datetime):
    """POST /iridium to Go relay (JWT skipped in dev mode).

    Matches real RockBLOCK payload format:
      {"call":"KF8ABL-12","lat":422949,"lon":-837107,"alt":2,"dir":238,"spd":0,"v":46,"t":1937}
    Where lat/lon are *10000, v is voltage*10, t is uptime seconds.
    """
    inner_payload = json.dumps({
        "call": callsign,
        "lat": int(round(lat * 10000)),
        "lon": int(round(lon * 10000)),
        "alt": int(alt_m),
        "dir": course,
        "spd": int(round(speed_ms)),
        "v": batt_v_x10,
        "t": uptime_s,
    })
    data_hex = inner_payload.encode("utf-8").hex()

    resp = requests.post(f"{relay_url}/iridium", json={
        "momsn": int(time.time()) % 100000,
        "imei": imei,
        "data": data_hex,
        "serial": 12345,
        "device_type": "ROCKBLOCK",
        "iridium_latitude": lat + 0.01,
        "iridium_longitude": lon + 0.01,
        "iridium_cep": 3.0,
        "transmit_time": ts.strftime("%y-%m-%d %H:%M:%S"),
        "JWT": "dev-mode-skip",
    }, timeout=5)
    return resp.status_code, resp.text.strip()


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test umich-balloons relay + APRS-IS")
    parser.add_argument("--relay", default="http://localhost:8080", help="Go relay base URL")
    parser.add_argument("--no-aprs-is", action="store_true", help="Skip APRS-IS injection")
    parser.add_argument("--ticks", type=int, default=NUM_TICKS, help="Number of 60s ticks")
    parser.add_argument("--fresh", action="store_true", help="Ignore saved state, start from scratch")
    args = parser.parse_args()

    relay_url = args.relay.rstrip("/")
    use_aprs_is = not args.no_aprs_is

    # Try to resume from previous state
    resumed = False
    prev = None if args.fresh else load_state()
    if prev:
        start_lat = prev["lat"]
        start_lon = prev["lon"]
        start_alt = prev["alt"]
        start_tick = prev["tick"]
        age_min = (time.time() - prev["timestamp"]) / 60
        resumed = True
    else:
        start_lat = START_LAT
        start_lon = START_LON
        start_alt = START_ALT
        start_tick = 0

    print("=" * 70)
    print("  umich-balloons End-to-End Test")
    print("=" * 70)
    print(f"  Balloon:      {BALLOON_CALLSIGN}")
    print(f"  Relay:        {relay_url}")
    print(f"  APRS-IS:      {'enabled' if use_aprs_is else 'disabled'}")
    print(f"  Ticks:        {args.ticks} x {TICK_SECONDS}s = {args.ticks * TICK_SECONDS / 60:.0f} min")
    if resumed:
        print(f"  Resuming:     from ({start_lat:.4f}, {start_lon:.4f}) at {start_alt:.0f}m (tick {start_tick}, {age_min:.1f} min ago)")
    else:
        print(f"  Start:        {START_LAT:.4f}, {START_LON:.4f}, {START_ALT:.0f}m (fresh)")
    print()

    # Check relay health
    try:
        r = requests.get(f"{relay_url}/health", timeout=5)
        r.raise_for_status()
        print(f"  Relay health: OK")
    except Exception as e:
        print(f"  ERROR: Relay not reachable at {relay_url}: {e}")
        print(f"  Make sure the Go relay is running (make run)")
        sys.exit(1)

    # Connect APRS-IS
    aprs_is = None
    if use_aprs_is:
        print()
        print("Connecting to APRS-IS...")
        try:
            aprs_is = APRSISConnection(APRS_IS_LOGIN_CALL, APRS_IS_PASSCODE)
            aprs_is.connect()
        except Exception as e:
            print(f"  WARNING: APRS-IS connection failed: {e}")
            print(f"  Continuing without APRS-IS")
            aprs_is = None

    print()
    print("=" * 70)
    print("  WHAT TO EXPECT")
    print("=" * 70)
    print()
    print("  Data will be uploaded to SondeHub for real.")
    print("  You should see:")
    print("    - Go relay logs showing queued packets for all 3 relay paths")
    print("    - SondeHub upload logs (batched every 2s)")
    if aprs_is:
        print("    - APRS-IS packets visible on aprs.fi within ~30s")
        print(f"      https://aprs.fi/#!call=a/{BALLOON_CALLSIGN}")
        print()
        print("  NOTE: SondeHub monitors APRS-IS for balloon symbol 'O' packets.")
        print("  If your callsign is new to SondeHub, it may take ~5 min to appear.")
    print()
    print("  SondeHub tracker (may take a few minutes for new callsigns):")
    print(f"    {SONDEHUB_TRACKER_URL}/?callsign={BALLOON_CALLSIGN}")
    print()
    print("=" * 70)
    print()

    # Number of active methods (for stagger calculation)
    num_methods = 4  # APRS-IS, APRS relay, LoRa relay, Iridium
    stagger = TICK_SECONDS / num_methods  # 15s between each method

    lat = start_lat
    lon = start_lon
    alt = start_alt

    for tick_offset in range(args.ticks):
        tick = start_tick + tick_offset
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        course = 45  # NE
        speed_knots = 5
        speed_ms = speed_knots * 0.514444
        batt_v_x10 = 46 - tick  # voltage*10: 4.6V draining slowly
        batt_mv = batt_v_x10 * 100  # for LoRa (millivolts)
        sats = 12
        temp = -10.0 - tick * 2  # getting colder as it climbs
        uptime_s = tick * TICK_SECONDS  # simulated uptime

        # Per-tick drift (split evenly across the 4 sends)
        tick_dlat = DRIFT_LAT + 0.0005 * math.sin(tick * 0.5)
        tick_dlon = DRIFT_LON + 0.0005 * math.cos(tick * 0.3)
        tick_dalt = CLIMB_RATE
        step_dlat = tick_dlat / num_methods
        step_dlon = tick_dlon / num_methods
        step_dalt = tick_dalt / num_methods

        print(f"[{now_str}] Tick {tick_offset + 1}/{args.ticks}  "
              f"pos=({lat:.4f}, {lon:.4f})  alt={alt:.0f}m  v={batt_v_x10/10:.1f}V")

        # 1. APRS-IS (sync to :00)
        ts = wait_until_second(0)
        if aprs_is:
            try:
                pkt = build_aprs_packet(BALLOON_CALLSIGN, lat, lon, alt, course, speed_knots)
                aprs_is.send_packet(pkt)
                print(f"  [:{ts.second:02d}] APRS-IS:    sent  ({pkt[:60]}...)")
            except Exception as e:
                print(f"  [:{ts.second:02d}] APRS-IS:    FAIL  {e}")
        else:
            print(f"  [:{ts.second:02d}] APRS-IS:    skipped")

        lat += step_dlat; lon += step_dlon; alt += step_dalt

        # 2. APRS relay (sync to :15)
        ts = wait_until_second(15)
        try:
            code, body = send_aprs_relay(relay_url, BALLOON_CALLSIGN, lat, lon, alt, course, speed_knots, ts)
            print(f"  [:{ts.second:02d}] APRS relay: {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:{ts.second:02d}] APRS relay: FAIL  {e}")

        lat += step_dlat; lon += step_dlon; alt += step_dalt

        # 3. LoRa relay (sync to :30)
        ts = wait_until_second(30)
        try:
            code, body = send_lora_relay(relay_url, BALLOON_CALLSIGN, lat, lon, alt,
                                         course, speed_ms, batt_mv, sats, temp, ts)
            print(f"  [:{ts.second:02d}] LoRa relay: {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:{ts.second:02d}] LoRa relay: FAIL  {e}")

        lat += step_dlat; lon += step_dlon; alt += step_dalt

        # 4. Iridium relay (sync to :45)
        ts = wait_until_second(45)
        try:
            code, body = send_iridium_relay(relay_url, IRIDIUM_IMEI, BALLOON_CALLSIGN,
                                            lat, lon, alt, course, speed_ms,
                                            batt_v_x10, uptime_s, ts)
            print(f"  [:{ts.second:02d}] Iridium:    {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:{ts.second:02d}] Iridium:    FAIL  {e}")

        lat += step_dlat; lon += step_dlon; alt += step_dalt

        print()

        # Save state after each tick so we can resume
        save_state(lat, lon, alt, tick + 1)

    print("=" * 70)
    print("  DONE")
    print("=" * 70)
    print()
    print(f"  Final position: ({lat:.4f}, {lon:.4f}) at {alt:.0f}m")
    print()
    print("  Check results:")
    if aprs_is:
        print(f"    aprs.fi:   https://aprs.fi/#!call=a/{BALLOON_CALLSIGN}")
    print(f"    SondeHub:  {SONDEHUB_TRACKER_URL}/?callsign={BALLOON_CALLSIGN}")
    print()
    if aprs_is:
        print("  APRS-IS data should be visible on aprs.fi now.")
        print("  SondeHub picks up APRS-IS balloon packets automatically (symbol 'O').")
    print()

    if aprs_is:
        aprs_is.close()
        print("  APRS-IS connection closed.")


if __name__ == "__main__":
    main()
