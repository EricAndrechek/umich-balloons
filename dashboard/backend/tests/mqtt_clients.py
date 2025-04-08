import paho.mqtt.client as paho_mqtt
import threading
import time
import random
import json
import sys
import aprspy

import os
import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("MQTT")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

from delays import get_truncated_normal_delay


mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
mqtt_port = int(os.getenv("MQTT_PORT", 1883))

# --- Delay Configuration ---
DELAY_MU = 5.0          # Mean delay in seconds (center of distribution)
DELAY_SIGMA = 10.0      # Standard deviation for delay (spread)
DELAY_MIN = 0.0         # Minimum delay in seconds
DELAY_MAX = 60.0        # Maximum delay in seconds

SUCCESS_RATE = 0.8

# idea is to have several simultaneous MQTT clients connected under different callsigns
# which will all send telemetry to the broker with variable amounts of delay


# Dictionary to hold our client objects and their connection status
mqtt_clients = {}
_clients_lock = threading.Lock() # To safely access the mqtt_clients dict

# --- Callback Functions (shared by all clients, but run in their context) ---

def on_connect(client, userdata, flags, rc, properties=None):
    """Callback when a client connects to the broker."""
    client_id = client._client_id.decode('utf-8') # Get client ID
    if rc == 0:
        with _clients_lock:
            if client_id in mqtt_clients:
                mqtt_clients[client_id]['connected'] = True
        # send status message
        publish_message(client_id, "status", "online", qos=1, retain=True)
    else:
        log.warning(f"[{client_id}]: Client Connection failed (rc={rc})")
        with _clients_lock:
            if client_id in mqtt_clients:
                mqtt_clients[client_id]['connected'] = False

def on_disconnect(client, userdata, rc, properties=None):
    """Callback when a client disconnects from the broker."""
    client_id = client._client_id.decode('utf-8')
    log.warning(f"[{client_id}]: Client Disconnected (rc={rc})")
    with _clients_lock:
        if client_id in mqtt_clients:
            mqtt_clients[client_id]['connected'] = False

def on_publish(client, userdata, mid):
    """Callback when a message is successfully published (for QoS > 0)."""
    client_id = client._client_id.decode('utf-8')
    log.debug(f"[{client_id}]: Client Message Published (mid={mid})")

def on_log(client, userdata, level, buf):
    """Optional: Callback for logging Paho internal messages."""
    client_id = client._client_id.decode("utf-8")
    log.debug(f"[{client_id}]: Log: {buf}")

# --- Client Management Functions ---

def create_and_connect_client(client_id):
    """Creates, configures, and connects a single MQTT client."""

    password = aprspy.utils.APRSUtils().generate_passcode(client_id)

    client = paho_mqtt.Client(client_id=client_id, protocol=paho_mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    client.on_log = on_log

    client.username_pw_set(client_id, password)
    client.will_set(f"{client_id}/status", payload="offline", qos=1, retain=True)

    with _clients_lock:
        mqtt_clients[client_id] = {'client': client, 'connected': False}

    try:
        client.connect_async(mqtt_broker, mqtt_port, keepalive=65535)
        client.loop_start()
        log.debug(f"[{client_id}]: Client Connection initiated...") # Less verbose
        return client_id
    except Exception as e:
        log.warning(f"[{client_id}]: Client Error initiating connection: {e}")
        client.loop_stop()
        with _clients_lock:
            del mqtt_clients[client_id]
        return None

def publish_message(client_id, topic, payload, qos=1, retain=False):
    """Publishes a message using a specific client (immediately)."""
    with _clients_lock:
        client_info = mqtt_clients.get(client_id)

    if client_info and client_info['connected']:
        client = client_info['client']
        try:
            log.info(f"[{client_id}]: Publishing message to {topic} with payload: {payload}")
            result = client.publish(f"{client_id}/{topic}", payload=payload, qos=qos, retain=retain)
            if result.rc != paho_mqtt.MQTT_ERR_SUCCESS:
                log.warning(f"[{client_id}]: Client Error queuing publish (rc={result.rc})")
                return False
            return True
        except Exception as e:
            log.warning(f"[{client_id}]: Client Exception during publish: {e}")
            return False
    elif client_info:
        log.warning(f"[{client_id}]: Client cannot publish: Not connected.")
        return False
    else:
        # This case should ideally not happen if called correctly
        log.warning(f"[{client_id}]: Client ID not found for publishing.")
        return False

def disconnect_all_clients():
    """Disconnects all managed clients gracefully."""
    log.info("Disconnecting all clients...")
    client_ids_to_remove = []
    with _clients_lock:
        client_ids_to_remove = list(mqtt_clients.keys())

        for client_id in client_ids_to_remove:
            client_info = mqtt_clients.get(client_id)
            if client_info:
                client = client_info['client']
                log.debug(f"[{client_id}]: Stopping loop and disconnecting client...")
                client.loop_stop()
                client.disconnect()
                time.sleep(0.05) # Tiny sleep per client

    time.sleep(1) # Allow disconnect callbacks
    with _clients_lock:
        mqtt_clients.clear()
    log.info("All clients disconnected.")

def _delayed_publish_worker(client_id, topic, payload, qos, retain, delay):
    """
    Worker function executed in a thread. Waits for the delay,
    then calls publish_message.
    """
    try:
        time.sleep(delay)
        publish_message(client_id, topic, payload, qos, retain)
    except Exception as e:
        log.error(f"[{client_id}]: Error in delay worker thread: {e}")

def broadcast_with_delay(topic, payload, qos=1, retain=False, ground_stations=[]):
    """
    Sends a message to a topic from all connected clients, each after
    a unique randomized delay sampled from a truncated normal distribution.
    """
    threads = []
    with _clients_lock:
        # Create a list of connected client IDs first to avoid issues if
        # a client disconnects while we are iterating
        connected_client_ids = [
            cid for cid, info in mqtt_clients.items() if info['connected']
        ]

    if not connected_client_ids:
        log.warning("No connected clients to broadcast.")
        return
    
    # only send to the ground stations that are connected
    connected_targets = []
    for station in ground_stations:
        if station in connected_client_ids:
            connected_targets.append(station)
        else:
            log.warning(f"Ground station {station} is not connected. Skipping.")
            continue
    
    if not connected_targets:
        log.warning("No connected ground stations to broadcast.")
        return

    for client_id in connected_targets:
        # Randomly decide whether to publish based on SUCCESS_RATE
        if random.random() > SUCCESS_RATE:
            log.info(f"[{client_id}]: Pretending offline. No packet sent.")
            continue

        # Calculate unique delay for this client
        delay = get_truncated_normal_delay(
            DELAY_MU,
            DELAY_SIGMA,
            DELAY_MIN,
            DELAY_MAX
        )

        # Create and start a thread for this client's delayed publish
        thread = threading.Thread(
            target=_delayed_publish_worker,
            args=(client_id, topic, payload, qos, retain, delay),
            daemon=True # Daemon threads exit automatically if main program exits
        )
        threads.append(thread)
        thread.start()

    log.debug(f"Launched {len(threads)} delayed publish threads.")
    # Note: This function returns immediately after starting threads.
    # Publishing happens concurrently in the background.
