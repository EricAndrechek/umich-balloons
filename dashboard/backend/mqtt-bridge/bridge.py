# Script to connect to MQTT broker and Redis and:
# publish +/aprs, +/lora, and +/status MQTT messages from MQTT --> Redis 
# and to publish Redis "sync" messages --> MQTT broker under +/sync

import paho.mqtt.client as mqtt
import redis
from redis import Redis
from redis.client import PubSub
import os
import json
import logging
from datetime import datetime

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_BRIDGE_USERNAME = os.getenv("MQTT_BRIDGE_USERNAME", "user")
MQTT_BRIDGE_PASSWORD = os.getenv("MQTT_BRIDGE_PASSWORD", "password")

# Configure logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

redis_client = None
mqtt_client = None

def setup_redis():
    global redis_client
    try:
        logger.info(f"Connecting to Redis at {REDIS_URL}")
        redis_client = Redis.from_url(REDIS_URL, db=0)
        redis_client.ping()  # Test the connection
        logger.info("Redis connection established successfully.")
    except redis.ConnectionError as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise
    return redis_client

def setup_mqtt():
    global mqtt_client

    logger.info(f"Connecting to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.username_pw_set(MQTT_BRIDGE_USERNAME, MQTT_BRIDGE_PASSWORD)

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(client, userdata, flags, reason_code, properties):
        logger.info(f"Connected to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} with result code {reason_code}")

        res = client.subscribe("+/aprs", 1)
        logger.info(f"Subscribed to topic +/aprs with result: {res}")
        res = client.subscribe("+/lora", 1)
        logger.info(f"Subscribed to topic +/lora with result: {res}")
        res = client.subscribe("+/status", 1)
        logger.info(f"Subscribed to topic +/status with result: {res}")

        logger.info("Subscribed to topics: +/aprs, +/lora, +/status")

        # tell the broker that this client (the bridge) is online
        res = client.publish("BRIDGE/status", payload="online", qos=1, retain=True)
        logger.info(f"Published online status to topic BRIDGE/status with result: {res}")

    def on_message(client, userdata, msg):
        logger.info("on_message called")
        topic = msg.topic
        payload = msg.payload.decode('utf-8')
        logger.debug(f"Received message on topic {topic}: {payload}")
        logger.info(f"Received message on topic {topic}: {payload}")
        # Publish the message to Redis
        publish_to_redis(topic, payload)
        logger.info(f"Published message to Redis topic {topic}: {payload}")

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.will_set("BRIDGE/status", payload="offline", qos=1, retain=True)

    try:
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        logger.info(f"Connected to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    except Exception as e:
        logger.error(f"Failed to connect to MQTT broker: {e}")
        raise

    return mqtt_client

def publish_to_mqtt(topic, payload):
    if mqtt_client is not None:
        try:
            mqtt_client.publish(topic, payload)
            logger.info(f"Published message to topic {topic}: {payload}")
        except Exception as e:
            logger.error(f"Failed to publish message to MQTT: {e}")
    else:
        logger.warning("MQTT client is not initialized. Cannot publish message.")

def publish_to_redis(topic, payload):
    if redis_client is not None:
        try:

            data = {
                "sender": topic.split("/")[0],  # Split the topic into parts
                "payload": payload,
                "timestamp": datetime.utcnow().isoformat()
            }

            # if the topic falls under the "+/status" wildcard, publish to the "status" channel
            if topic.endswith("/status"):
                redis_client.publish("status", json.dumps(data))

            # not a "pubsub" channel, but rather a parsing task, so just rpush
            elif topic.endswith("/aprs"):
                redis_client.rpush("aprs", json.dumps(data))
            elif topic.endswith("/lora"):
                redis_client.rpush("lora", json.dumps(data))

            else:
                logger.warning(f"Unknown topic format: {topic}. Not publishing to Redis.")
                return
            
            logger.info(f"Published message to Redis {topic}: {payload}")
        except Exception as e:
            logger.error(f"Failed to publish message to Redis: {e}")
    else:
        logger.warning("Redis client is not initialized. Cannot publish message.")

def main():
    global mqtt_client, redis_client
    try:
        # Setup Redis and MQTT clients
        redis_client = setup_redis()
        mqtt_client = setup_mqtt()

        # Start the MQTT loop in a separate thread
        mqtt_client.loop_start()

        # Subscribe to Redis channels and publish messages to MQTT
        pubsub = redis_client.pubsub()
        pubsub.subscribe("sync")

        logger.info("Listening for messages on Redis channels...")
        for message in pubsub.listen():
            if message['type'] == 'message':
                topic = message['channel'].decode('utf-8')
                payload = message['data'].decode('utf-8')
                logger.debug(f"Received message on Redis channel {topic}: {payload}")
                # Publish the message to MQTT
                publish_to_mqtt("+/sync", payload)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if mqtt_client is not None:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        if redis_client is not None:
            redis_client.close()
        logger.info("Disconnected from Redis and MQTT broker.")
        logger.info("Exiting...")
        exit(0)

if __name__ == "__main__":
    logger.info("Starting MQTT-Redis bridge...")
    logger.info(f"Redis URL: {REDIS_URL}")
    logger.info(f"MQTT Broker Host: {MQTT_BROKER_HOST}")
    logger.info(f"MQTT Broker Port: {MQTT_BROKER_PORT}")
    logger.info(f"MQTT Bridge Username: {MQTT_BRIDGE_USERNAME}")
    logger.info(f"MQTT Bridge Password: {MQTT_BRIDGE_PASSWORD}")
    main()