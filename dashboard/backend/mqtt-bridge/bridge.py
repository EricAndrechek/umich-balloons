import os
import json
import logging
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import redis

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
TASK_QUEUE_NAME = "raw_message_queue"
GATEWAY_LAST_SEEN_PREFIX = "gateway:last_seen:"
HEARTBEAT_INTERVAL_SECONDS = 600  # Match Pi's interval for expiry calculation

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.getenv(
    "MQTT_BRIDGE_USER", "bridge_user"
)  # Dedicated user for the bridge
MQTT_PASSWORD = os.getenv(
    "MQTT_BRIDGE_PASSWORD", "bridge_password"
)  # Set in .env and Mosquitto passwd file
MQTT_DATA_TOPIC = "gateways/+/data"  # Subscribe to data from all gateways
MQTT_STATUS_TOPIC = "gateways/+/status"  # Subscribe to status updates

# --- Redis Client (Using blocking client here for simplicity) ---
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    logger.info("Connected to Redis successfully.")
except redis.RedisError as e:
    logger.error(f"Failed to connect to Redis: {e}")
    exit(1)


# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info("Connected to MQTT Broker successfully.")
        # Subscribe on connect/reconnect
        client.subscribe(MQTT_DATA_TOPIC, qos=1)
        logger.info(f"Subscribed to data topic: {MQTT_DATA_TOPIC}")
        client.subscribe(MQTT_STATUS_TOPIC, qos=1)
        logger.info(f"Subscribed to status topic: {MQTT_STATUS_TOPIC}")
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")


def on_disconnect(client, userdata, rc, properties=None):
    logger.warning(
        f"Disconnected from MQTT Broker, return code {rc}. Will attempt reconnection."
    )


def update_gateway_status(gateway_id: str, payload_str: str):
    """Process status messages (online/offline from LWT)."""
    try:
        payload = json.loads(payload_str)
        is_online = payload.get("online", False)  # Expecting {"online": true/false}
        logger.info(
            f"Status update for {gateway_id}: {'Online' if is_online else 'Offline'}"
        )
        # Update Redis last_seen only if online, let expiry handle offline timeout
        if is_online:
            now_iso = datetime.now(timezone.utc).isoformat()
            redis_client.set(
                f"{GATEWAY_LAST_SEEN_PREFIX}{gateway_id}",
                now_iso,
                ex=HEARTBEAT_INTERVAL_SECONDS * 3,
            )
        # Optionally: Store explicit offline status in Redis/DB if needed beyond timeout
        # else: redis_client.set(f"{GATEWAY_STATUS_PREFIX}{gateway_id}", "offline")

    except (json.JSONDecodeError, Exception) as e:
        logger.error(
            f"Failed to process status message for {gateway_id}: {e}, Payload: {payload_str}"
        )


def queue_data_message(gateway_id: str, payload_bytes: bytes):
    """Format and push data message to Redis queue."""
    try:
        # Assume payload_bytes is the JSON string sent by the Pi
        raw_message_payload = json.loads(payload_bytes.decode("utf-8"))

        # Construct task data matching Task Handler's expectation
        # We need to map the Pi's payload format to RawMessageTaskData format
        task_data = {
            "identifier_type": raw_message_payload.get(
                "type", "unknown"
            ),  # Pi needs to send this
            "identifier_value": raw_message_payload.get("id"),  # Pi needs to send this
            "source": raw_message_payload.get("source", "MQTT Gateway"),
            "source_id": raw_message_payload.get("via"),  # Optional info from Pi packet
            "raw_data": raw_message_payload.get(
                "raw"
            ),  # Pi needs to send the raw packet string
            "data_time": raw_message_payload.get(
                "time"
            ),  # Pi sends timestamp if available (ISO format)
            "gateway_id": gateway_id,  # Extracted from topic
        }
        task_data_json = json.dumps(task_data)

        redis_client.lpush(TASK_QUEUE_NAME, task_data_json)
        logger.debug(f"Queued data message from gateway {gateway_id}")

    except (json.JSONDecodeError, UnicodeDecodeError, redis.RedisError, Exception) as e:
        logger.error(
            f"Failed to queue data message from {gateway_id}: {e}, Payload: {payload_bytes[:100]}..."
        )
        # Consider dead-letter queue


def on_message(client, userdata, msg):
    logger.debug(f"Received message on topic {msg.topic}")
    try:
        topic_parts = msg.topic.split("/")
        if len(topic_parts) >= 3:
            gateway_id = topic_parts[1]
            message_type = topic_parts[2]

            if message_type == "data":
                queue_data_message(gateway_id, msg.payload)
            elif message_type == "status":
                update_gateway_status(gateway_id, msg.payload.decode("utf-8"))
            else:
                logger.warning(
                    f"Unknown message type '{message_type}' on topic {msg.topic}"
                )
        else:
            logger.warning(f"Unexpected topic structure: {msg.topic}")

    except Exception as e:
        logger.exception(f"Error processing message on topic {msg.topic}: {e}")


# --- MQTT Client Setup ---
mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2, client_id="mqtt-bridge-service"
)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

# Set username/password
if MQTT_USERNAME and MQTT_PASSWORD:
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
else:
    logger.warning(
        "MQTT username/password not set. Connecting anonymously (if allowed by broker)."
    )

# Attempt connection (with automatic reconnect loop)
mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)

# Start network loop (blocking)
logger.info("Starting MQTT loop...")
mqtt_client.loop_forever()  # Handles reconnects automatically
