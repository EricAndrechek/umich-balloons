import redis.asyncio as redis
import asyncio
import json
from fastapi import WebSocket

from .config import settings
from ..services.connection_manager import manager  # Import connection manager

redis_pubsub_client: redis.Redis | None = None
redis_cache_client: redis.Redis | None = None

pubsub_listener_task: asyncio.Task | None = None

import logging

log = logging.getLogger(__name__)

async def create_redis_clients():
    """Creates Redis clients for Pub/Sub and Caching."""
    global redis_pubsub_client, redis_cache_client

    try:
        redis_pubsub_client = redis.from_url(
            settings.REDIS_URL,
            db=settings.REDIS_QUEUE_DB,
            decode_responses=True,
            health_check_interval=30,
        )
        redis_cache_client = redis.from_url(
            settings.REDIS_URL,
            db=settings.REDIS_CACHE_DB,
            decode_responses=True,
            health_check_interval=30,
        )

        # Perform checks
        await redis_pubsub_client.ping()
        log.info("Redis Pub/Sub connection successful.")
        await redis_cache_client.ping()
        log.info("Redis Cache connection successful.")

    except Exception as e:
        log.error(
            f"Failed to connect to one or more Redis instances: {e}", exc_info=True
        )
        # Clean up partially connected clients if necessary
        if redis_pubsub_client:
            await redis_pubsub_client.close()
        if redis_cache_client:
            await redis_cache_client.close()
        redis_pubsub_client = None
        redis_cache_client = None
        raise  # Re-raise to signal connection failure


async def close_redis_clients():
    """Closes Redis connections."""
    global redis_pubsub_client, redis_cache_client
    tasks = []
    if redis_pubsub_client:
        log.info("Closing Redis Pub/Sub connection...")
        tasks.append(redis_pubsub_client.close())
        redis_pubsub_client = None
    if redis_cache_client:
        log.info("Closing Redis Cache connection...")
        tasks.append(redis_cache_client.close())
        redis_cache_client = None
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)  # Close concurrently
    log.info("Redis connections closed.")


def get_redis_pubsub_client():
    if not redis_pubsub_client:
        raise Exception("Redis Pub/Sub client not initialized.")
    return redis_pubsub_client


def get_redis_cache_client():
    if not redis_cache_client:
        raise Exception("Redis Cache client not initialized.")
    return redis_cache_client


async def pubsub_listener():
    """Listens to Redis Pub/Sub for real-time updates and broadcasts."""
    if not redis_pubsub_client:
        return  # Should not happen if called after connect_redis

    pubsub = redis_pubsub_client.pubsub()
    await pubsub.subscribe(settings.REDIS_UPDATES_CHANNEL, "raw-messages")
    log.info(f"Subscribed to Redis channels: {settings.REDIS_UPDATES_CHANNEL}, raw-messages")

    while True:
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=None
            )  # Blocks until message
            if message and message["type"] == "message":
                log.info(f"Received message from Redis: {message['data']}")  # Debug
                try:
                    channel = message["channel"]

                    if channel == settings.REDIS_UPDATES_CHANNEL:
                        data = json.loads(message["data"])
                        geohash_str = data.get("geohash_str")
                        if geohash_str:
                            # Prepare payload for clients
                            payload = {
                                "type": "newPosition",  # Define event types clearly
                                "data": data,
                            }
                            log.info(f"Broadcasting to room {geohash_str}: {payload}")
                            # Broadcast using the connection manager
                            await manager.broadcast_to_room(
                                geohash_str, json.dumps(payload)
                            )
                        else:
                            log.warning("Warning: Redis message missing geohash_str")
                    elif channel == "raw-messages":
                        # Broadcast raw messages to all connected clients
                        await manager.broadcast_raw_msg(message["data"])
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
