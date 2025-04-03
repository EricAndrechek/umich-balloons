import redis.asyncio as redis
import asyncio
import json
from fastapi import WebSocket

from .config import settings
from ..services.connection_manager import manager  # Import connection manager

redis_client: redis.Redis | None = None
pubsub_listener_task: asyncio.Task | None = None

import logging

log = logging.getLogger(__name__)


async def connect_redis():
    """Connects to Redis."""
    global redis_client
    try:
        redis_client = redis.from_url(settings.REDIS_URL, db=settings.REDIS_QUEUE_DB, decode_responses=True)
        await redis_client.ping()
        log.debug("Connected to Redis successfully.")
    except Exception as e:
        log.error(f"Error connecting to Redis: {e}")
        raise


async def close_redis():
    """Closes the Redis connection."""
    global redis_client
    if redis_client:
        await redis_client.close()
        log.debug("Redis connection closed.")


async def get_redis():
    """Dependency to get the Redis client."""
    if not redis_client:
        raise Exception("Redis client not initialized.")
    return redis_client


async def pubsub_listener():
    """Listens to Redis Pub/Sub for real-time updates and broadcasts."""
    if not redis_client:
        return  # Should not happen if called after connect_redis

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(settings.REDIS_UPDATES_CHANNEL)
    log.info(f"Subscribed to Redis channel: {settings.REDIS_UPDATES_CHANNEL}")

    while True:
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=None
            )  # Blocks until message
            if message and message["type"] == "message":
                log.info(f"Received message from Redis: {message['data']}")  # Debug
                try:
                    data = json.loads(message["data"])
                    grid_cell_id = data.get("grid_cell_id")
                    if grid_cell_id:
                        # Prepare payload for clients
                        payload = {
                            "type": "newPosition",  # Define event types clearly
                            "data": {
                                "payload_id": data.get("payload_id"),
                                "telemetry_id": data.get("telemetry_id"),
                                "lat": data.get("lat"),
                                "lon": data.get("lon"),
                                "ts": data.get("ts"),
                            },
                        }
                        # Broadcast using the connection manager
                        await manager.broadcast_to_room(
                            grid_cell_id, json.dumps(payload)
                        )
                    else:
                        log.warning("Warning: Redis message missing grid_cell_id")
                except json.JSONDecodeError:
                    log.error(f"Error decoding JSON from Redis message: {message['data']}")
                except Exception as e:
                    log.error(f"Error processing Redis message: {e}")

        except asyncio.CancelledError:
            log.info("Pub/Sub listener task cancelled.")
            break
        except redis.ConnectionError as e:
            log.error(
                f"Redis connection error in listener: {e}. Attempting to reconnect..."
            )
            await asyncio.sleep(5)  # Wait before attempting resubscribe
            # Re-establish pubsub - simple retry here, robust apps might need more logic
            try:
                await pubsub.subscribe(settings.REDIS_UPDATES_CHANNEL)
                log.info("Resubscribed to Redis channel after connection error.")
            except Exception as re_e:
                log.error(f"Failed to resubscribe after connection error: {re_e}")
                await asyncio.sleep(10)  # Longer wait if resubscribe fails
        except Exception as e:
            log.error(f"Unexpected error in Redis pubsub_listener: {e}")
            await asyncio.sleep(5)  # Prevent fast spinning on unexpected errors


async def start_pubsub_listener():
    global pubsub_listener_task
    if not pubsub_listener_task or pubsub_listener_task.done():
        log.info("Starting Redis Pub/Sub listener task...")
        pubsub_listener_task = asyncio.create_task(pubsub_listener())
    else:
        log.info("Pub/Sub listener task already running.")


async def stop_pubsub_listener():
    global pubsub_listener_task
    if pubsub_listener_task and not pubsub_listener_task.done():
        log.info("Stopping Redis Pub/Sub listener task...")
        pubsub_listener_task.cancel()
        try:
            await pubsub_listener_task
        except asyncio.CancelledError:
            log.info("Pub/Sub listener task stopped successfully.")
        pubsub_listener_task = None
