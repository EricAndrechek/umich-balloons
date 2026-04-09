# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "aprslib"]
# ///
"""
End-to-end test for umich-balloons relay and APRS-IS -> SondeHub pipeline.

Simulates a balloon drifting NE from Ann Arbor, ascending slowly.
Forms a single telemetry message per tick and sends it via four paths,
staggered evenly across each 60s tick:
  1. APRS-IS  – raw ! position packet injected into the real APRS-IS network (:00)
  2. APRS push – POST /aprs on the relay with ground station timestamp (:15)
  3. LoRa push – POST /lora on the relay with t=HHMM field (:30)
  4. Iridium   – POST /iridium on the relay with t=HHMM field (:45)

Every 3 ticks, an old packet is re-sent via LoRa or Iridium (with a fresh
timestamp but identical position and t field) to test SondeHub deduplication.

State is saved per entity in a SQLite database (.test_state.db) and
auto-resumes within 1 hour.  When resuming, the position is extrapolated
forward based on elapsed time so telemetry graphs look continuous.

Usage:
  uv run test_sondehub.py                                   # default: KF8ABL-11 + KD8CJT-9
  uv run test_sondehub.py -b KF8ABL-12 -s KD8CJT-8          # specific combo
  uv run test_sondehub.py -b KF8ABL-13 -s KD8CJT-9          # another combo
  uv run test_sondehub.py --live                              # deployed worker
  uv run test_sondehub.py --no-aprs-is --fresh

Prerequisites:
  - Relay running: `pnpm dev` (local) or deployed via `pnpm deploy`
  - For local dev: DEV_MODE=true in wrangler.toml skips Iridium JWT check
  - A valid APRS-IS passcode for your callsign
"""

import argparse
import json
import math
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_BALLOON = "KF8ABL-11"
DEFAULT_STATION = "KD8CJT-9"
APRS_IS_PASSCODE = "19121"
IRIDIUM_IMEI = "301434060500780"

# Start near Ann Arbor, MI
START_LAT = 42.2808
START_LON = -83.7430
START_ALT = 300.0  # meters

# Chase car starts slightly south-west of the balloon launch site
CHASE_START_LAT = 42.2780
CHASE_START_LON = -83.7460
CHASE_ALT = 260.0  # ground level

# Movement per tick
DRIFT_LAT = 0.002   # ~220m north per minute
DRIFT_LON = 0.003   # ~250m east per minute
CLIMB_RATE = 150.0   # meters per minute

STATE_DIR = Path(__file__).parent
RESUME_MAX_AGE_S = 3600  # 1 hour

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

# ── State persistence (SQLite) ─────────────────────────────────────────────

STATE_DB = STATE_DIR / ".test_state.db"


def _get_db():
    """Open (and auto-create) the state database, returning a connection."""
    db = sqlite3.connect(STATE_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS entity_state (
            kind      TEXT NOT NULL,
            callsign  TEXT NOT NULL,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            alt       REAL,
            tick      INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            PRIMARY KEY (kind, callsign)
        )
    """)
    return db


def _extrapolate(state: dict, elapsed_ticks: int, is_chase: bool):
    """Advance a state dict forward by elapsed_ticks using the drift model."""
    tick_base = state["tick"]
    for i in range(elapsed_ticks):
        t = tick_base + i
        dlat = DRIFT_LAT + 0.0005 * math.sin(t * 0.5)
        dlon = DRIFT_LON + 0.0005 * math.cos(t * 0.3)
        if is_chase:
            state["lat"] += dlat * 0.3
            state["lon"] += dlon * 0.3
        else:
            state["lat"] += dlat
            state["lon"] += dlon
            state["alt"] += CLIMB_RATE
    state["tick"] += elapsed_ticks


def save_balloon_state(callsign: str, lat: float, lon: float, alt: float, tick: int):
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO entity_state (kind, callsign, lat, lon, alt, tick, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("balloon", callsign, lat, lon, alt, tick, time.time()),
    )
    db.commit()
    db.close()


def save_station_state(callsign: str, lat: float, lon: float, tick: int):
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO entity_state (kind, callsign, lat, lon, alt, tick, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("station", callsign, lat, lon, None, tick, time.time()),
    )
    db.commit()
    db.close()


def load_entity_state(kind: str, callsign: str, is_chase: bool):
    """Load and extrapolate state for a balloon or station.

    Returns (state_dict, age_seconds, extrapolated_ticks) or None.
    """
    db = _get_db()
    row = db.execute(
        "SELECT lat, lon, alt, tick, timestamp FROM entity_state WHERE kind = ? AND callsign = ?",
        (kind, callsign),
    ).fetchone()
    db.close()
    if row is None:
        return None
    state = {"lat": row[0], "lon": row[1], "alt": row[2] or 0.0, "tick": row[3]}
    age = time.time() - row[4]
    if age > RESUME_MAX_AGE_S:
        return None
    elapsed = int(age / TICK_SECONDS)
    if elapsed >= 1:
        _extrapolate(state, elapsed, is_chase)
    return state, age, elapsed

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
    """Build an uncompressed APRS position packet with balloon symbol 'O'.

    Matches the real balloon format:
      KF8ABL-12>APRS,WIDE2-1,qAR,W8UM:!4217.69N/08342.65WO346/000/A=000901
    Uses ! (no timestamp) since the real tracker doesn't include APRS timestamps.
    Path uses TCPIP* for APRS-IS TCP injection (the server adds qAR/qAO).
    """
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
    info = f"!{lat_str}/{lon_str}O{course:03d}/{speed_knots:03d}/A={alt_feet:06d}"
    return f"{callsign}>APRS,TCPIP*:{info}"


# ── Relay Senders ───────────────────────────────────────────────────────────

def send_aprs_relay(relay_url: str, callsign: str, lat: float, lon: float,
                    alt_m: float, course: int, speed_knots: int,
                    sender: str, ts: datetime):
    """POST /aprs to Go relay."""
    packet = build_aprs_packet(callsign, lat, lon, alt_m, course, speed_knots)
    resp = requests.post(f"{relay_url}/aprs", json={
        "sender": sender,
        "raw_data": packet,
        "timestamp": ts.isoformat(),
    }, timeout=5)
    return resp.status_code, resp.text.strip()


def send_lora_relay(relay_url: str, callsign: str, lat: float, lon: float,
                    alt_m: float, course: int, speed_ms: float, batt_mv: int,
                    sats: int, temp: float, t_hhmm: int,
                    sender: str, ts: datetime):
    """POST /lora to Go relay.

    Matches real balloon firmware compact format (same as Iridium inner payload):
      {"call":"KF8ABL-11","lat":422949,"lon":-837107,"alt":3,"dir":45,"spd":2,"v":46,"t":1937}
    Where lat/lon are *10000, alt is in hectometers (m/100), v is voltage*10.
    """
    resp = requests.post(f"{relay_url}/lora", json={
        "sender": sender,
        "raw_data": {
            "call": callsign,
            "lat": int(round(lat * 10000)),
            "lon": int(round(lon * 10000)),
            "alt": int(alt_m / 100),
            "dir": course,
            "spd": int(round(speed_ms)),
            "v": int(batt_mv / 100),
            "sats": sats,
            "temp": temp,
            "t": t_hhmm,
        },
        "timestamp": ts.isoformat(),
    }, timeout=5)
    return resp.status_code, resp.text.strip()


def send_iridium_relay(relay_url: str, imei: str, callsign: str,
                       lat: float, lon: float, alt_m: float,
                       course: int, speed_ms: float,
                       batt_v_x10: int, t_hhmm: int, ts: datetime):
    """POST /iridium to Go relay (JWT skipped in dev mode).

    Matches real RockBLOCK payload format:
      {"call":"KF8ABL-11","lat":422949,"lon":-837107,"alt":3,"dir":238,"spd":0,"v":46,"t":1937}
    Where lat/lon are *10000, alt is in hectometers (m/100), v is voltage*10,
    t is HHMM UTC (e.g. 1937 = 19:37).
    """
    inner_payload = json.dumps({
        "call": callsign,
        "lat": int(round(lat * 10000)),
        "lon": int(round(lon * 10000)),
        "alt": int(alt_m / 100),
        "dir": course,
        "spd": int(round(speed_ms)),
        "v": batt_v_x10,
        "t": t_hhmm,
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


def send_station_position(relay_url: str, callsign: str, lat: float, lon: float,
                          alt: float):
    """POST /station to relay (chase vehicle position)."""
    resp = requests.post(f"{relay_url}/station", json={
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "alt": alt,
    }, timeout=5)
    return resp.status_code, resp.text.strip()


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test umich-balloons relay + APRS-IS")
    parser.add_argument("-b", "--balloon", default=DEFAULT_BALLOON,
                        help=f"Balloon callsign (default: {DEFAULT_BALLOON})")
    parser.add_argument("-s", "--station", default=DEFAULT_STATION,
                        help=f"Ground station callsign, also used as chase car (default: {DEFAULT_STATION})")
    parser.add_argument("--relay", default=None, help="Relay base URL (default: http://localhost:8787)")
    parser.add_argument("--live", nargs="?", const="https://api.umich-balloons.com", default=None,
                        metavar="URL", help="Use deployed worker (default: https://api.umich-balloons.com)")
    parser.add_argument("--no-aprs-is", action="store_true", help="Skip APRS-IS injection")
    parser.add_argument("--ticks", type=int, default=NUM_TICKS, help="Number of 60s ticks")
    parser.add_argument("--fresh", action="store_true", help="Ignore saved state, start from scratch")
    args = parser.parse_args()

    balloon_call = args.balloon
    station_call = args.station

    if args.live is not None:
        relay_url = args.live.rstrip("/")
    elif args.relay is not None:
        relay_url = args.relay.rstrip("/")
    else:
        relay_url = "http://localhost:8787"
    use_aprs_is = not args.no_aprs_is

    # Load per-entity state (balloon and station independently)
    balloon_prev = None if args.fresh else load_entity_state("balloon", balloon_call, is_chase=False)
    station_prev = None if args.fresh else load_entity_state("station", station_call, is_chase=True)

    if balloon_prev:
        bp, b_age, b_extrap = balloon_prev
        start_lat = bp["lat"]
        start_lon = bp["lon"]
        start_alt = bp["alt"]
        start_tick = bp["tick"]
    else:
        start_lat = START_LAT
        start_lon = START_LON
        start_alt = START_ALT
        start_tick = 0

    if station_prev:
        sp, s_age, s_extrap = station_prev
        start_chase_lat = sp["lat"]
        start_chase_lon = sp["lon"]
    else:
        start_chase_lat = CHASE_START_LAT
        start_chase_lon = CHASE_START_LON

    print("=" * 70)
    print("  umich-balloons End-to-End Test")
    print("=" * 70)
    print(f"  Balloon:      {balloon_call}")
    print(f"  Station:      {station_call}")
    print(f"  Relay:        {relay_url}")
    print(f"  APRS-IS:      {'enabled' if use_aprs_is else 'disabled'}")
    print(f"  Ticks:        {args.ticks} x {TICK_SECONDS}s = {args.ticks * TICK_SECONDS / 60:.0f} min")
    if balloon_prev:
        print(f"  Balloon:      resuming tick {start_tick} at ({start_lat:.4f}, {start_lon:.4f}) {start_alt:.0f}m"
              f" ({b_age/60:.1f} min ago, +{b_extrap} extrapolated)")
    else:
        print(f"  Balloon:      fresh from {START_LAT:.4f}, {START_LON:.4f}, {START_ALT:.0f}m")
    if station_prev:
        print(f"  Chase car:    resuming at ({start_chase_lat:.4f}, {start_chase_lon:.4f})"
              f" ({s_age/60:.1f} min ago, +{s_extrap} extrapolated)")
    else:
        print(f"  Chase car:    fresh from {CHASE_START_LAT:.4f}, {CHASE_START_LON:.4f}")
    print()

    # Check relay health
    try:
        r = requests.get(f"{relay_url}/health", timeout=5)
        r.raise_for_status()
        print(f"  Relay health: OK")
    except Exception as e:
        print(f"  ERROR: Relay not reachable at {relay_url}: {e}")
        print(f"  Make sure the relay is running (pnpm dev)")
        sys.exit(1)

    # Connect APRS-IS
    aprs_is = None
    if use_aprs_is:
        print()
        print("Connecting to APRS-IS...")
        try:
            aprs_is = APRSISConnection(station_call, APRS_IS_PASSCODE)
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
    print("    - Relay logs showing accepted packets for all 3 relay paths")
    print("    - SondeHub upload forwarded immediately per packet")
    print(f"    - Chase car '{station_call}' moving on SondeHub tracker map")
    if aprs_is:
        print("    - APRS-IS packets visible on aprs.fi within ~30s")
        print(f"      https://aprs.fi/#!call=a/{balloon_call}")
        print()
        print("  NOTE: SondeHub monitors APRS-IS for balloon symbol 'O' packets.")
        print("  If your callsign is new to SondeHub, it may take ~5 min to appear.")
    print()
    print("  SondeHub tracker (may take a few minutes for new callsigns):")
    print(f"    {SONDEHUB_TRACKER_URL}/?callsign={balloon_call}")
    print()
    print("=" * 70)
    print()

    lat = start_lat
    lon = start_lon
    alt = start_alt

    # Chase car position (tracks balloon slowly on the ground)
    chase_lat = start_chase_lat
    chase_lon = start_chase_lon

    # History of sent messages for delayed re-send (dedup testing)
    sent_history: list[dict] = []

    for tick_offset in range(args.ticks):
        tick = start_tick + tick_offset
        course = 45  # NE
        speed_knots = 5
        speed_ms = speed_knots * 0.514444
        batt_v_x10 = 46 - tick  # voltage*10: 4.6V draining slowly
        batt_mv = batt_v_x10 * 100  # for LoRa (millivolts)
        sats = 12
        temp = -10.0 - tick * 2  # getting colder as it climbs

        # Per-tick drift
        tick_dlat = DRIFT_LAT + 0.0005 * math.sin(tick * 0.5)
        tick_dlon = DRIFT_LON + 0.0005 * math.cos(tick * 0.3)
        tick_dalt = CLIMB_RATE

        # Single position for this tick (all paths get the same coords)
        tick_lat = lat
        tick_lon = lon
        tick_alt = alt

        # ── Sync to :00 — generate all packets with this timestamp ──
        secs_until = (60 - time.time() % 60) % 60
        if secs_until > 0.05:
            print(f"  Waiting {secs_until:.0f}s for :00...")
        gen_ts = wait_until_second(0)

        # t field: HHMM UTC at the moment of packet generation
        t_hhmm = gen_ts.hour * 100 + gen_ts.minute

        now_str = gen_ts.strftime("%H:%M:%S")
        print(f"[{now_str}] Tick {tick_offset + 1}/{args.ticks}  "
              f"pos=({tick_lat:.4f}, {tick_lon:.4f})  alt={tick_alt:.0f}m  "
              f"v={batt_v_x10/10:.1f}V  t={t_hhmm:04d}")

        # Save this tick's data for potential delayed re-send
        sent_history.append({
            "lat": tick_lat, "lon": tick_lon, "alt": tick_alt,
            "course": course, "speed_ms": speed_ms, "speed_knots": speed_knots,
            "batt_v_x10": batt_v_x10, "batt_mv": batt_mv,
            "sats": sats, "temp": temp, "t_hhmm": t_hhmm,
            "tick": tick,
        })

        # 1. APRS-IS (already at :00, send immediately)
        if aprs_is:
            try:
                pkt = build_aprs_packet(balloon_call, tick_lat, tick_lon, tick_alt,
                                        course, speed_knots)
                aprs_is.send_packet(pkt)
                print(f"  [:{gen_ts.second:02d}] APRS-IS:    sent  ({pkt[:70]}...)")
            except Exception as e:
                print(f"  [:{gen_ts.second:02d}] APRS-IS:    FAIL  {e}")
        else:
            print(f"  [:{gen_ts.second:02d}] APRS-IS:    skipped")

        # Persist state right after first send so a Ctrl-C mid-tick doesn't lose it
        save_balloon_state(balloon_call, tick_lat, tick_lon, tick_alt, tick)
        save_station_state(station_call, chase_lat, chase_lon, tick)

        # 2. APRS relay (sync to :15) — same position, gen_ts timestamp
        wait_until_second(15)
        try:
            code, body = send_aprs_relay(relay_url, balloon_call,
                                         tick_lat, tick_lon, tick_alt,
                                         course, speed_knots, station_call, gen_ts)
            print(f"  [:15] APRS relay: {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:15] APRS relay: FAIL  {e}")

        # 3. LoRa relay (sync to :30) — same position, gen_ts timestamp
        wait_until_second(30)
        try:
            code, body = send_lora_relay(relay_url, balloon_call,
                                         tick_lat, tick_lon, tick_alt,
                                         course, speed_ms, batt_mv,
                                         sats, temp, t_hhmm, station_call, gen_ts)
            print(f"  [:30] LoRa relay: {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:30] LoRa relay: FAIL  {e}")

        # 4. Iridium relay (sync to :45) — same position, gen_ts timestamp
        wait_until_second(45)
        try:
            code, body = send_iridium_relay(relay_url, IRIDIUM_IMEI, balloon_call,
                                            tick_lat, tick_lon, tick_alt,
                                            course, speed_ms,
                                            batt_v_x10, t_hhmm, gen_ts)
            print(f"  [:45] Iridium:    {code}  {body[:50]}")
        except Exception as e:
            print(f"  [:45] Iridium:    FAIL  {e}")

        # 5. Delayed re-send for dedup testing (every 3 ticks, re-send an old packet)
        if tick_offset > 0 and tick_offset % 3 == 0 and len(sent_history) >= 3:
            old = sent_history[-3]  # packet from 2 ticks ago
            resend_ts = datetime.now(timezone.utc)
            via = "LoRa" if tick_offset % 6 == 0 else "Iridium"
            try:
                if via == "LoRa":
                    code, body = send_lora_relay(
                        relay_url, balloon_call,
                        old["lat"], old["lon"], old["alt"],
                        old["course"], old["speed_ms"], old["batt_mv"],
                        old["sats"], old["temp"], old["t_hhmm"],
                        station_call, resend_ts)
                else:
                    code, body = send_iridium_relay(
                        relay_url, IRIDIUM_IMEI, balloon_call,
                        old["lat"], old["lon"], old["alt"],
                        old["course"], old["speed_ms"],
                        old["batt_v_x10"], old["t_hhmm"], resend_ts)
                print(f"  [DEDUP]    {via} re-send (tick {old['tick']}, t={old['t_hhmm']:04d}): {code}  {body[:50]}")
            except Exception as e:
                print(f"  [DEDUP]    {via} re-send FAIL: {e}")

        # 6. Ground station chase vehicle position (every tick, no sync needed)
        chase_lat += tick_dlat * 0.3  # chase car follows balloon slowly
        chase_lon += tick_dlon * 0.3
        try:
            code, body = send_station_position(relay_url, station_call,
                                                chase_lat, chase_lon, CHASE_ALT)
            now_ts = datetime.now(timezone.utc)
            print(f"  [:{now_ts.second:02d}] Station:    {code}  {body[:50]}")
        except Exception as e:
            now_ts = datetime.now(timezone.utc)
            print(f"  [:{now_ts.second:02d}] Station:    FAIL  {e}")

        print()

        # Advance position for next tick
        lat += tick_dlat
        lon += tick_dlon
        alt += tick_dalt

    print("=" * 70)
    print("  DONE")
    print("=" * 70)
    print()
    print(f"  Final position: ({lat:.4f}, {lon:.4f}) at {alt:.0f}m")
    print()
    print("  Check results:")
    if aprs_is:
        print(f"    aprs.fi:   https://aprs.fi/#!call=a/{balloon_call}")
    print(f"    SondeHub:  {SONDEHUB_TRACKER_URL}/?callsign={balloon_call}")
    print(f"    Chase car: should appear as '{station_call}' on the SondeHub map")
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
