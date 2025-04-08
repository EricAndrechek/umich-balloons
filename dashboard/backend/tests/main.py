import os
from re import L
import time
import json
import requests
import logging
from datetime import datetime
import threading
import uuid
import random # For random delays
import math
import json
import h3
import traceback

import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("MAIN")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Testing parameters
NUM_BALLOONS = 3            # how many balloons we want to simulate
NUM_GROUND_STATIONS = 3     # how many ground stations we want to simulate

GROUND_STATION_CALLSIGN = "UMCAR"
GS_CALLSIGNS = [f"{GROUND_STATION_CALLSIGN}-{i}" for i in range(1, NUM_GROUND_STATIONS + 1)]

BALLOON_CALLSIGN = "BLOON"
BALLOON_CALLSIGNS = [f"{BALLOON_CALLSIGN}-{i}" for i in range(1, NUM_BALLOONS + 1)]

NUM_PAYLOADS = NUM_BALLOONS + NUM_GROUND_STATIONS
CALLSIGNS = BALLOON_CALLSIGNS + GS_CALLSIGNS

# ingress methods to test
HTTP = True
# MQTT is not used since we can't tunnel it out, so false
MQTT = False
# unclear how to test APRS-IS without spamming real APRS-IS servers
# so we don't want to do this now
# could self-host aprsc for testing in the future, but aren't now
APRS_IS = False

# data sources
APRS = True
LORA = True
# iridium is harder to test since they must be signed by the iridium network
# in the future we could have a testing key to allow, but don't have that yet
IRIDIUM = False 

# how many seconds between each telemetry message
APRS_PERIOD = 60
LORA_PERIOD = 60
IRIDIUM_PERIOD = 60

# --- Random Payload + Ground Station Walk ---
START_LAT = 42.2808
START_LON = -83.7430

# period to wait before updating the balloon locations
# should be more frequent that the period between any telemetry messages
UPDATE_PERIOD = 5  # seconds

# we aren't using MQTT since we can't tunnel it out, so removing this import
if MQTT:
    from mqtt_clients import (
        create_and_connect_client,
        broadcast_with_delay,
        disconnect_all_clients,
    )
if HTTP:
    from http_clients import broadcast_message

if APRS:
    from build_aprs import build_aprs_str
if LORA:
    pass
if IRIDIUM:
    pass

payload_locations = [
    (START_LAT + random.uniform(-0.01, 0.01), START_LON + random.uniform(-0.01, 0.01))
    for _ in range(NUM_PAYLOADS)
]

def walk_payloads(left=0.0025, right=0.005, up=0.0025, down=0.0005):
    """
    Simulates the movement of payloads.
    Each payload's latitude and longitude are adjusted randomly within a small range.

    left: max drift in degrees to the left
    right: max drift in degrees to the right
    up: max drift in degrees up
    down: max drift in degrees down

    returns the new positions for each payload
    """

    new_locations = []

    for i in range(NUM_PAYLOADS):
        # Randomly adjust the latitude and longitude
        lat_drift = random.uniform(-left, right)
        lon_drift = random.uniform(-down, up)

        # get current location to adjust
        lat, lon = payload_locations[i]

        # update the location
        lat += lat_drift
        lon += lon_drift

        # Ensure the latitude and longitude are within valid ranges
        # if latitude is out of bounds, wrap it around (ie 90 + 1 = -89)
        if lat > 90:
            log.warning(
                f"{CALLSIGNS[i]}: Latitude out of bounds: {lat} -> wrapping"
            )
            lat = -90 + (lat - 90)
        elif lat < -90:
            log.warning(
                f"{CALLSIGNS[i]}: Latitude out of bounds: {lat} -> wrapping"
            )
            lat = 90 + (lat + 90)
        # if longitude is out of bounds, wrap it around (ie 180 + 1 = -179)
        if lon > 180:
            log.warning(
                f"{CALLSIGNS[i]}: Longitude out of bounds: {lon} -> wrapping"
            )
            lon = -180 + (lon - 180)
        elif lon < -180:
            log.warning(
                f"{CALLSIGNS[i]}: Longitude out of bounds: {lon} -> wrapping"
            )
            lon = 180 + (lon + 180)
        
        new_locations.append((lat, lon))

    # return the changes
    return new_locations

def transmit_iridium():
    if IRIDIUM:
        while True:
            for i in range(NUM_BALLOONS):
                lat, lon = payload_locations[i]
                callsign = BALLOON_CALLSIGNS[i]
                # build the packet
                message_payload = {
                    "call": callsign,
                    "lat": lat,
                    "lon": lon,
                    "alt": random.randint(0, 10000),
                    "cse": random.randint(0, 360),
                    "spd": random.uniform(0, 100),
                }
                endpoint = "iridium"
                # iridium only goes to HTTP
                if HTTP:
                    # send the packet to the HTTP API
                    # iridium is also unique as it comes right from the balloon, not a ground station
                    broadcast_message(message_payload, endpoint, BALLOON_CALLSIGNS)

                time.sleep(IRIDIUM_PERIOD / NUM_PAYLOADS)

def transmit_lora():
    # function runs forever packeting each payload and ground station location as a LORA packet (with random telemetry other than location)
    # then send it to the MQTT broker and HTTP API
    if LORA:
        while True:
            for i in range(NUM_PAYLOADS):
                lat, lon = payload_locations[i]
                callsign = CALLSIGNS[i]
                # build the LORA packet
                message_payload = {
                    "call": callsign,
                    "lat": lat,
                    "lon": lon,
                    "alt": random.randint(0, 10000),
                    "cse": random.randint(0, 360),
                    "spd": random.uniform(0, 100),
                }
                endpoint = "lora"
                if MQTT:
                    # send the LORA packet to the MQTT broker
                    broadcast_with_delay(
                        topic=endpoint,
                        payload=json.dumps(message_payload),
                        qos=1,  # Example: Use QoS 1 for this broadcast
                    )
                if HTTP:
                    # send the LORA packet to the HTTP API
                    broadcast_message(message_payload, endpoint, GS_CALLSIGNS)

                time.sleep(LORA_PERIOD / NUM_PAYLOADS)

def transmit_aprs():
    # function runs forever packeting each payload and ground station location as an APRS packet (with random telemetry other than location)
    # then send it to the MQTT broker and HTTP API
    if APRS:
        while True:
            for i in range(NUM_PAYLOADS):
                lat, lon = payload_locations[i]
                callsign = CALLSIGNS[i]
                # build the APRS packet
                symbol = ("/", "O")  # Default symbol for payloads
                if i >= NUM_BALLOONS:
                    # use car symbol
                    symbol = ("/", ">")
                message_payload = build_aprs_str(callsign, lat, lon, symbol=symbol)
                # send the APRS packet to the MQTT broker
                endpoint = "aprs"
                
                # only send from all ground stations if it isn't a ground station
                senders = GS_CALLSIGNS if i < NUM_BALLOONS else [CALLSIGNS[i]]
                if MQTT:
                    broadcast_with_delay(
                        topic=endpoint,
                        payload=message_payload,
                        qos=1,  # Example: Use QoS 1 for this broadcast
                        ground_stations=senders,
                    )
                if HTTP:
                    # send the APRS packet to the HTTP API
                    broadcast_message(message_payload, endpoint, senders)
                if APRS_IS:
                    # send the APRS packet to the APRS-IS server
                    # this is not implemented yet, but could be done with a self-hosted aprsc server
                    pass

                time.sleep(APRS_PERIOD / NUM_PAYLOADS)


def run_data_sources():
    global payload_locations

    active_mqtt_client_ids = []
    active_aprs_is_client_ids = []

    if MQTT:
        log.info(f"Attempting to start {NUM_GROUND_STATIONS} MQTT clients...")

        for i in range(NUM_GROUND_STATIONS):

            client_id = create_and_connect_client(GS_CALLSIGNS[i])
            if client_id:
                active_mqtt_client_ids.append(client_id)
            time.sleep(0.05) # Slightly stagger connections

    if APRS_IS:
        log.info(f"Attempting to start {NUM_GROUND_STATIONS} APRS-IS clients...")

        for i in range(NUM_GROUND_STATIONS):
            # TODO: Implement APRS-IS client connection
            client_id = GS_CALLSIGNS[i]  # Placeholder for APRS-IS client ID
            if client_id:
                active_aprs_is_client_ids.append(client_id)
            time.sleep(0.05)

    if APRS_IS or MQTT:
        log.info("Waiting a few seconds for connections to establish...")
        time.sleep(5) # Give clients time to connect

        log.info(f"Connected APRS-IS clients: {active_aprs_is_client_ids}")
        log.info(f"Connected MQTT clients: {active_mqtt_client_ids}")

    # run the transmission functions in separate threads to avoid blocking
    # stagger the start of each by (max(APRS_PERIOD, LORA_PERIOD, IRIDIUM_PERIOD) / sum(APRS, LORA, IRIDIUM)) * i
    THREAD_START_DELAY = max(APRS_PERIOD, LORA_PERIOD, IRIDIUM_PERIOD) / sum([APRS, LORA, IRIDIUM])
    if APRS:
        aprs_thread = threading.Thread(target=transmit_aprs, daemon=True)
        aprs_thread.start()
    if LORA:
        lora_thread = threading.Thread(target=transmit_lora, daemon=True)
        if APRS:
            # stagger the start of the LORA thread by THREAD_START_DELAY
            log.warning(f"Delaying LORA thread by {THREAD_START_DELAY} seconds start to avoid collisions with APRS.")
            timer = threading.Timer(THREAD_START_DELAY, lora_thread.start)
            timer.start()
        else:
            lora_thread.start()
    if IRIDIUM:
        iridium_thread = threading.Thread(target=transmit_iridium, daemon=True)
        if APRS or LORA:
            # stagger the start of the IRIDIUM thread by THREAD_START_DELAY
            log.warning(f"Delaying IRIDIUM thread by {THREAD_START_DELAY * sum([APRS, LORA])} seconds start to avoid collisions with APRS and/or LORA.")
            timer = threading.Timer(THREAD_START_DELAY * sum([APRS, LORA]), iridium_thread.start)
            timer.start()
        else:
            iridium_thread.start()

    log.info("Clients are running. Broadcasts happening in background.")
    log.info("Press Ctrl+C to disconnect and exit.")

    try:
        while True:
            # Simulate payload movement
            new_locations = walk_payloads()
            for i, (new_lat, new_lon) in enumerate(new_locations):
                lat, lon = payload_locations[i]

                km_moved = h3.great_circle_distance((lat, lon), (new_lat, new_lon), "km")
                speed = abs(km_moved) / (UPDATE_PERIOD / 3600)  # km/h

                # Update the payload location
                payload_locations[i] = (new_lat, new_lon)

                log.info(f"[{CALLSIGNS[i]}]: moved {km_moved:.2f} km at {speed:.2f} km/h")
                if speed >= 500:
                    log.warning(f"[{CALLSIGNS[i]}]: moving too fast! Speed: {speed:.2f} km/h")

            time.sleep(UPDATE_PERIOD)  # Wait before updating again

    except KeyboardInterrupt:
        log.warning("Ctrl+C received.")
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        log.error(traceback.format_exc())
    finally:
        # --- Cleanup ---
        if MQTT:
            disconnect_all_clients()
        if APRS_IS:
            pass
        log.info("Program finished.")

# --- Main Execution ---

if __name__ == "__main__":
    # Check if the script is being run directly
    log.info("Starting telemetry simulation...")
    run_data_sources()
    log.info("Telemetry simulation finished.")
