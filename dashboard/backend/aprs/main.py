import asyncio
import logging
import os
import signal
import json
import sys
from typing import Set, Optional, Dict, Any

import redis.asyncio as redis
import aprs
from aprs import exceptions as aprs_exceptions

# --- Configuration (from Environment Variables) ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CALLSIGN_SUB_CHANNEL = os.getenv(
    "REDIS_CALLSIGN_SUB_CHANNEL", "aprs_callsigns_to_monitor"
)
REDIS_APRS_PUB_CHANNEL = os.getenv("REDIS_APRS_PUB_CHANNEL", "aprs_data_feed")

APRS_IS_HOST = os.getenv("APRS_IS_HOST", "rotate.aprs.net")  # Use rotation service
APRS_IS_PORT = int(os.getenv("APRS_IS_PORT", 14580))  # Standard read-only port
APRS_IS_USER = os.getenv("APRS_IS_USER", "N0CALL")  # Replace with your callsign
APRS_IS_PASSCODE = os.getenv(
    "APRS_IS_PASSCODE", "-1"
)  # Generate one for your callsign!

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Logging Setup ---
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,  # Log to stdout for Docker
)
logger = logging.getLogger("APRSListener")

# --- Global State ---
# Use a set for efficient checking of monitored callsigns
monitored_callsigns: Set[str] = set()
# Lock to safely update the set from different async tasks
callsign_lock = asyncio.Lock()
# Redis client instances (will be initialized later)
redis_pub_client: Optional[redis.Redis] = None
redis_sub_client: Optional[redis.Redis] = None


# --- APRS Packet Handling ---
async def process_aprs_packet(packet: Dict[str, Any]):
    """Checks if the packet source is monitored and publishes to Redis if it is."""
    global monitored_callsigns
    global redis_pub_client
    global callsign_lock

    source_callsign = packet.get("from")
    if not source_callsign:
        logger.debug("Packet missing 'from' field: %s", packet.get("raw", "N/A"))
        return

    async with callsign_lock:
        is_monitored = source_callsign in monitored_callsigns

    if is_monitored:
        logger.info("Received monitored packet from %s", source_callsign)
        if redis_pub_client:
            try:
                # Add packet metadata if desired
                # packet_to_publish = packet
                packet_to_publish = {
                    "raw": packet.get("raw"),
                    "from": packet.get("from"),
                    "to": packet.get("to"),
                    "via": packet.get("via"),
                    "type": packet.get("format"),
                    # Add specific parsed fields based on packet type if needed
                    # Example:
                    "latitude": packet.get("latitude"),
                    "longitude": packet.get("longitude"),
                    "comment": packet.get("comment"),
                    "timestamp": packet.get("timestamp"),
                    "symbol": packet.get("symbol"),
                    "symbol_table": packet.get("symbol_table"),
                    "altitude": packet.get("altitude"),
                    "course": packet.get("course"),
                    "speed": packet.get("speed"),
                }
                # Filter out None values for cleaner JSON
                filtered_packet = {
                    k: v for k, v in packet_to_publish.items() if v is not None
                }

                await redis_pub_client.publish(
                    REDIS_APRS_PUB_CHANNEL, json.dumps(filtered_packet)
                )
                logger.debug(
                    "Published packet from %s to Redis channel %s",
                    source_callsign,
                    REDIS_APRS_PUB_CHANNEL,
                )
            except redis.RedisError as e:
                logger.error("Redis publish error: %s", e)
            except Exception as e:
                logger.error(
                    "Error processing or publishing packet: %s", e, exc_info=True
                )
        else:
            logger.warning("Redis publish client not available, cannot publish packet.")
    else:
        logger.debug("Ignoring packet from unmonitored callsign %s", source_callsign)


# --- APRS-IS Connection Task ---
async def aprs_listener_task():
    """Connects to APRS-IS and listens for packets."""
    while True:
        logger.info(
            "Attempting to connect to APRS-IS: %s:%d", APRS_IS_HOST, APRS_IS_PORT
        )
        aprs_client = aprs.aioclient.IS(
            APRS_IS_USER, APRS_IS_PASSCODE, host=APRS_IS_HOST, port=APRS_IS_PORT
        )
        # Set the callback function
        aprs_client.add_handler(process_aprs_packet)

        try:
            # Connect and block until disconnected or error
            # No filter is applied here; we filter based on the Redis list in process_aprs_packet
            await aprs_client.connect()

        except aprs_exceptions.ConnectionError as e:
            logger.error("APRS-IS Connection Error: %s", e)
        except aprs_exceptions.LoginError as e:
            logger.error("APRS-IS Login Error: %s. Check user/passcode.", e)
            logger.error("Exiting due to fatal login error.")
            # You might want to sys.exit(1) here or implement a more robust shutdown
            await asyncio.sleep(60)  # Wait before retrying if not exiting
        except asyncio.CancelledError:
            logger.info("APRS-IS listener task cancelled.")
            await aprs_client.close()  # Ensure client is closed on cancellation
            break  # Exit the loop on cancellation
        except Exception as e:
            logger.error("Unexpected error in APRS-IS listener: %s", e, exc_info=True)
        finally:
            # Ensure client is closed if connection loop exits unexpectedly
            if aprs_client and aprs_client.is_connected():
                await aprs_client.close()
                logger.info("APRS-IS connection closed.")

        logger.info("Disconnected from APRS-IS. Reconnecting in 30 seconds...")
        await asyncio.sleep(30)  # Wait before attempting to reconnect


# --- Redis Subscription Task ---
async def redis_subscriber_task():
    """Listens to Redis Pub/Sub for callsign list updates."""
    global monitored_callsigns
    global redis_sub_client
    global callsign_lock

    while True:
        try:
            logger.info("Connecting to Redis Pub/Sub...")
            redis_sub_client = redis.Redis.from_url(
                REDIS_URL, decode_responses=True
            )  # Decode responses to strings for JSON
            await redis_sub_client.ping()  # Check connection
            logger.info("Redis Pub/Sub connection successful.")

            async with redis_sub_client.pubsub() as pubsub:
                await pubsub.subscribe(REDIS_CALLSIGN_SUB_CHANNEL)
                logger.info(
                    "Subscribed to Redis channel: %s", REDIS_CALLSIGN_SUB_CHANNEL
                )

                while True:  # Listen for messages
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=60
                    )  # Timeout to periodically check connection
                    if message is None:
                        # Timeout occurred, check connection health
                        try:
                            await redis_sub_client.ping()
                        except redis.ConnectionError:
                            logger.warning("Redis connection lost during listen loop.")
                            break  # Break inner loop to trigger reconnection
                        continue  # Continue listening if ping succeeds

                    if message and message.get("type") == "message":
                        channel = message["channel"]
                        data_str = message["data"]
                        logger.info("Received message on Redis channel %s", channel)
                        logger.debug("Raw message data: %s", data_str)

                        try:
                            # Expecting a JSON list of strings
                            new_callsigns_list = json.loads(data_str)
                            if isinstance(new_callsigns_list, list) and all(
                                isinstance(cs, str) for cs in new_callsigns_list
                            ):
                                new_callsigns_set = set(
                                    cs.upper() for cs in new_callsigns_list
                                )  # Normalize to upper case
                                async with callsign_lock:
                                    # Efficiently find added and removed callsigns
                                    added = new_callsigns_set - monitored_callsigns
                                    removed = monitored_callsigns - new_callsigns_set
                                    monitored_callsigns = (
                                        new_callsigns_set  # Update the set
                                    )
                                logger.info(
                                    "Updated monitored callsigns. Total: %d. Added: %s. Removed: %s",
                                    len(monitored_callsigns),
                                    added or "None",
                                    removed or "None",
                                )
                                logger.debug(
                                    "Current monitored callsigns: %s",
                                    sorted(list(monitored_callsigns)),
                                )

                            else:
                                logger.warning(
                                    "Received invalid data format on %s. Expected JSON list of strings. Data: %s",
                                    channel,
                                    data_str,
                                )
                        except json.JSONDecodeError:
                            logger.warning(
                                "Failed to decode JSON from Redis message on %s. Data: %s",
                                channel,
                                data_str,
                            )
                        except Exception as e:
                            logger.error(
                                "Error processing Redis message: %s", e, exc_info=True
                            )

        except redis.ConnectionError as e:
            logger.error("Redis Pub/Sub Connection Error: %s", e)
        except asyncio.CancelledError:
            logger.info("Redis subscriber task cancelled.")
            break  # Exit the loop on cancellation
        except Exception as e:
            logger.error("Unexpected error in Redis subscriber: %s", e, exc_info=True)
        finally:
            if redis_sub_client:
                await redis_sub_client.close()
                logger.info("Redis Pub/Sub connection closed.")
                redis_sub_client = None  # Reset client

        logger.info("Disconnected from Redis Pub/Sub. Reconnecting in 15 seconds...")
        await asyncio.sleep(15)


# --- Main Application Logic ---
async def main():
    """Sets up connections and runs the listener tasks."""
    global redis_pub_client

    logger.info("Starting APRS-Redis Listener...")
    logger.info("--- Configuration ---")
    logger.info("REDIS_URL: %s", REDIS_URL)
    logger.info("REDIS_CALLSIGN_SUB_CHANNEL: %s", REDIS_CALLSIGN_SUB_CHANNEL)
    logger.info("REDIS_APRS_PUB_CHANNEL: %s", REDIS_APRS_PUB_CHANNEL)
    logger.info("APRS_IS_HOST: %s", APRS_IS_HOST)
    logger.info("APRS_IS_PORT: %d", APRS_IS_PORT)
    logger.info("APRS_IS_USER: %s", APRS_IS_USER)
    logger.info(
        "APRS_IS_PASSCODE: %s",
        "******" if APRS_IS_PASSCODE != "-1" else "Not Set/Invalid",
    )
    logger.info("LOG_LEVEL: %s", LOG_LEVEL)
    logger.info("---------------------")

    if APRS_IS_USER == "N0CALL" or APRS_IS_PASSCODE == "-1":
        logger.warning(
            "APRS_IS_USER is 'N0CALL' or APRS_IS_PASSCODE is '-1'. APRS-IS connection may fail or be rate-limited. Please configure with your callsign and passcode."
        )

    # Initialize Redis client for publishing
    try:
        redis_pub_client = redis.Redis.from_url(
            REDIS_URL, decode_responses=True
        )  # Decode responses to strings for JSON
        await redis_pub_client.ping()
        logger.info("Redis publish connection successful.")
    except redis.ConnectionError as e:
        logger.error(
            "Initial Redis publish connection failed: %s. Will attempt publishing later.",
            e,
        )
        # The process_aprs_packet function will handle redis_pub_client being None
    except Exception as e:
        logger.error(
            "Unexpected error initializing Redis publish client: %s", e, exc_info=True
        )

    # Create the main tasks
    redis_task = asyncio.create_task(redis_subscriber_task())
    aprs_task = asyncio.create_task(aprs_listener_task())

    # Wait for tasks to complete (they run forever until cancelled)
    done, pending = await asyncio.wait(
        {redis_task, aprs_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If one task finishes (likely due to an unhandled error or planned exit),
    # cancel the other task for graceful shutdown.
    for task in pending:
        logger.info("Cancelling pending task: %s", task.get_name())
        task.cancel()

    # Wait for pending tasks to finish cancellation
    if pending:
        await asyncio.wait(pending)

    # Close the publisher client connection if it exists
    if redis_pub_client:
        await redis_pub_client.close()
        logger.info("Redis publish connection closed.")

    logger.info("APRS-Redis Listener stopped.")


# --- Signal Handling for Graceful Shutdown ---
async def shutdown(signal, loop):
    """Graceful shutdown handler."""
    logger.warning(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
    [task.cancel() for task in tasks]

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All tasks cancelled.")
    loop.stop()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    # Add signal handlers for SIGINT (Ctrl+C) and SIGTERM (Docker stop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown(s, loop))
        )

    try:
        loop.run_until_complete(main())
    finally:
        logger.info("Shutting down event loop.")
        loop.close()
