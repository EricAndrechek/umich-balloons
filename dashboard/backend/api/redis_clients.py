import os
import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool
from redis.asyncio.client import Redis
from typing import Optional

# Import configuration (replace with your actual config loading)
REDIS_MESSAGES_DB = os.getenv("REDIS_MESSAGES_DB", 0)
REDIS_CACHE_DB = os.getenv("REDIS_CACHE_DB", 1)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

import logging
log = logging.getLogger(__name__)

# Global variables to hold the clients (initialized during startup)
redis_cache_client: Optional[Redis] = None
redis_message_client: Optional[Redis] = None
cache_pool: Optional[ConnectionPool] = None
message_pool: Optional[ConnectionPool] = None

async def create_redis_pools():
    """
    Creates Redis connection pools and clients during application startup.
    """
    global redis_cache_client, redis_message_client, cache_pool, message_pool
    try:
        log.info(f"Creating Redis connection pool for Cache (DB {REDIS_CACHE_DB})...")
        cache_pool = ConnectionPool.from_url(
            REDIS_URL,
            db=REDIS_CACHE_DB,
            decode_responses=True, # Decode responses to strings by default
            max_connections=10 # Adjust pool size as needed
        )
        redis_cache_client = Redis(connection_pool=cache_pool)
        await redis_cache_client.ping() # Verify connection
        log.info("Redis Cache connection pool created successfully.")

        log.info(f"Creating Redis connection pool for Messages (DB {REDIS_MESSAGES_DB})...")
        message_pool = ConnectionPool.from_url(
            REDIS_URL,
            db=REDIS_MESSAGES_DB,
            # Keep raw bytes for messages if needed, otherwise set decode_responses=True
            decode_responses=False,
            max_connections=10 # Adjust pool size as needed
        )
        redis_message_client = Redis(connection_pool=message_pool)
        await redis_message_client.ping() # Verify connection
        log.info("Redis Messages connection pool created successfully.")

    except Exception as e:
        log.error(f"Failed to create Redis connection pools: {e}")
        # Handle error appropriately (e.g., raise, exit, or retry logic)
        raise

async def close_redis_pools():
    """
    Closes Redis connections and pools during application shutdown.
    """
    global redis_cache_client, redis_message_client, cache_pool, message_pool
    log.info("Closing Redis connections...")
    if redis_cache_client:
        try:
            await redis_cache_client.close()
            log.info("Redis Cache client closed.")
        except Exception as e:
            log.error(f"Error closing Redis Cache client: {e}")
    if cache_pool:
        try:
            await cache_pool.disconnect()
            log.info("Redis Cache connection pool disconnected.")
        except Exception as e:
            log.error(f"Error disconnecting Redis Cache pool: {e}")

    if redis_message_client:
        try:
            await redis_message_client.close()
            log.info("Redis Messages client closed.")
        except Exception as e:
            log.error(f"Error closing Redis Messages client: {e}")
    if message_pool:
        try:
            await message_pool.disconnect()
            log.info("Redis Messages connection pool disconnected.")
        except Exception as e:
            log.error(f"Error disconnecting Redis Messages pool: {e}")

# --- Dependency Injection Functions ---

async def get_redis_cache() -> Redis:
    """
    Dependency function to get the Redis cache client.
    Raises an exception if the client is not available.
    """
    if not redis_cache_client:
        log.error("Redis cache client is not initialized.")
        raise RuntimeError("Redis cache client is not initialized.")
    return redis_cache_client

async def get_redis_message_db() -> Redis:
    """
    Dependency function to get the Redis message database client.
    Raises an exception if the client is not available.
    """
    if not redis_message_client:
        log.error("Redis message DB client is not initialized.")
        raise RuntimeError("Redis message DB client is not initialized.")
    return redis_message_client