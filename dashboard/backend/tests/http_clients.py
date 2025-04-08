import os
import requests
import json
import uuid
import random
from datetime import datetime, timezone
from delays import get_truncated_normal_delay
import threading
import time

import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("HTTP")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000/")

# --- Delay Configuration ---
DELAY_MU = 20.0          # Mean delay in seconds (center of distribution)
DELAY_SIGMA = 20.0      # Standard deviation for delay (spread)
DELAY_MIN = 0.0         # Minimum delay in seconds
DELAY_MAX = 60.0        # Maximum delay in seconds

# --- Packet Configuration ---
DUPLICATE_CHANCE = 0.5   # Chance to duplicate the packet
TIMESTAMP_CHANCE = 0.3   # Chance to add a timestamp
UUID_CHANCE = 0.3       # Chance to add a UUID
MAX_DUPLICATES = 10    # Maximum number of duplicates
SENDER_CHANCE = 0.8
SUCCESS_RATE = 0.8


def cut_middle(text, max_length=20, placeholder="..."):
    """Cuts out the middle of a string if it's longer than max_length."""
    if len(text) <= max_length:
        return text

    if text.startswith("http://") or text.startswith("https://"):
        # If the text is a URL, don't include the protocol in the cut
        return cut_middle(text.split("://", 1)[1], max_length, placeholder)

    half_length = (max_length - len(placeholder)) // 2
    return text[:half_length] + placeholder + text[-half_length:]


def broadcast_message(message, path, ground_stations):
    """
    Broadcast a message to all connected clients.
    """
    packet = {
        "raw_data": message,
    }
    
    for station in ground_stations:
        # simulate success rate
        if random.random() > SUCCESS_RATE:
            log.info(f"[{station}]: Pretending offline. No packet sent.")
            continue
        # run the send_with_variables function in a separate thread to avoid blocking
        threading.Thread(target=send_with_variables, args=(path, packet, station)).start()

def send_with_variables(route, packet, station):
    """
    Given a route and a packet, send the packet to the given route.
    Should have some % chance of duplicating the packet any number of times < 10.
    Should have x% chance of adding a timestamp to the packet.
    Should have y% chance of adding a uuid to the packet.
    """
    url = API_URL + route
    headers = {"Content-Type": "application/json"}
    payload = packet

    # % of the time add a UTC timestamp to the packet
    if random.random() < TIMESTAMP_CHANCE:
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()

    # % of the time add a UUID to the packet
    if random.random() < UUID_CHANCE:
        payload["message_id"] = str(uuid.uuid4())

    # % of the time add a sender to the packet
    if random.random() < SENDER_CHANCE:
        payload["sender"] = station

    # % chance to duplicate the packet, with each duplicate having a 50/50 chance to be duplicated again until max MAX_DUPLICATES
    num_duplicates = 0
    while num_duplicates < MAX_DUPLICATES and random.random() < DUPLICATE_CHANCE:
        num_duplicates += 1

    # pick a random delay (on a truncated normal distribution)
    delay = get_truncated_normal_delay(DELAY_MU, DELAY_SIGMA, DELAY_MIN, DELAY_MAX)

    log.info(
        f"{station}: Sending {num_duplicates + 1} packet{'s' if (num_duplicates + 1) != 1 else ''} to {cut_middle(url)} with delay {delay:.2f} seconds"
    )

    # send the packets evenly spaced out over the delay
    for _ in range(num_duplicates + 1):
        try:
            response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=5)

            # check if the response is valid JSON and a 202 status code
            if response.status_code != 202:
                log.error(f"[{station}]: Error: {response.status_code} - {response.text}")
            try:
                response_json = response.json()
                if not isinstance(response_json, dict):
                    raise ValueError("Response is not a valid JSON object")
                

            except json.JSONDecodeError:
                log.error(f"[{station}]: Error decoding JSON response: {response.text}")
            except ValueError as e:
                log.error(f"[{station}]: Error: {e}")
            
            log.debug(
                f"[{station}]: Response from {cut_middle(url)}: {response.status_code} - {response.text}"
            )

        except requests.exceptions.RequestException as e:
            log.error(f"[{station}]: Error sending packet: {e}")
        except json.JSONDecodeError as e:
            log.error(f"[{station}]: Error decoding JSON response: {e}")
        except Exception as e:
            log.error(f"[{station}]: Unexpected error: {e}")

        # wait for the delay before sending the next packet
        time.sleep(delay / (num_duplicates + 1))  # evenly distribute delay over all packets
