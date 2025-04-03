import asyncpg
from contextlib import asynccontextmanager
from fastapi import HTTPException, status
from typing import Any, Dict, Optional
import logging

from .config import settings

pool: asyncpg.Pool | None = None

log = logging.getLogger(__name__)

async def connect_db():
    """Creates the database connection pool."""
    global pool
    try:
        log.debug(f"Creating database connection pool with DSN: {settings.DATABASE_URL}")
        pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            # command_timeout=60, # Example: set command timeout
        )
        log.info("Database connection pool created successfully.")
    except Exception as e:
        log.error(f"Error creating database connection pool: {e}")
        # Optionally raise or exit if DB is critical at startup
        raise


async def close_db():
    """Closes the database connection pool."""
    global pool
    if pool:
        await pool.close()
        log.info("Database connection pool closed.")


@asynccontextmanager
async def get_db_connection():
    """Provides a connection from the pool."""
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection pool not available.",
        )
    conn = None
    try:
        # Acquire connection from pool
        conn = await pool.acquire()
        log.debug("Database connection acquired.")
        yield conn
    except Exception as e:
        log.error(f"Database connection error: {e}")
        # Handle specific DB errors if needed
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred.",
        ) from e
    finally:
        if conn:
            # Release connection back to pool
            await pool.release(conn)
