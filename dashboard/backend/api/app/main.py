import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import (
    Body,
    Depends, 
    FastAPI, 
    Header, 
    HTTPException, 
    Request,
    status
)

from fastapi.middleware.cors import CORSMiddleware

from .core import database, redis_client  # Relative imports
from .core.config import settings
from .routers import ingress, map_data, websockets

# --- Logging Configuration ---
logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger(__name__)

# --- Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Application startup...")
    await database.connect_db()
    await redis_client.connect_redis()
    await redis_client.start_pubsub_listener()  # Start the background listener task
    yield
    # Shutdown
    log.info("Application shutdown...")
    await redis_client.stop_pubsub_listener()
    await redis_client.close_redis()
    await database.close_db()
    log.info("Application shutdown complete.")


# --- FastAPI App Instance ---
app = FastAPI(
    title="Realtime Map Backend",
    description="Handles WebSocket connections and API calls for map data.",
    version="1.0.0",
    lifespan=lifespan,  # Use the lifespan context manager
)

# --- Middleware ---
# allow CORS for all origins
origins = ["*"]  # Change this to your frontend URL in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include Routers ---
app.include_router(map_data.router)
app.include_router(ingress.router)
# app.include_router(websockets.router)

# --- Basic Root Endpoint ---
@app.get("/")
async def read_root():
    """
    Root endpoint that returns a welcome message and basic information about the API.
    """
    return {"message": "Welcome to the Realtime Map Backend! Check the /docs for API details."}


# --- Health Endpoint ---
@app.get("/health", summary="Health Check")
async def health_check():
    """
    Health check endpoint to verify the service is running and connected to Redis and the Postgres database. Used primarily for Docker health checks.
    """

    # Check Redis connection
    try:
        await redis_client.redis_client.ping()
        redis_status = "OK"
    except Exception as e:
        redis_status = f"Error: {e}"

    # Check Database connection
    try:
        async with database.pool.acquire() as conn:
            await conn.execute("SELECT 1")
        db_status = "OK"
    except Exception as e:
        db_status = f"Error: {e}"

    # Build status based on health checks
    # ie if both are OK, status is OK
    # if either is not OK, status is degraded
    # if both are not OK, status is down
    if redis_status == "OK" and db_status == "OK":
        api_status = "OK"
    elif redis_status != "OK" or db_status != "OK":
        api_status = "Degraded"
    else:
        api_status = "Down"

    # Log the health status
    log.info(f"Health check status: {api_status}")
    log.info(f"Redis status: {redis_status}")
    log.info(f"Database status: {db_status}")
    # Log the timestamp
    log.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    # Return health status
    return {
        "status": api_status,
        "redis": redis_status,
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
