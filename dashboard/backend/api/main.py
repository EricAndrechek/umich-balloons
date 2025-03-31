import os
import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, status, Header, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager

from redis_clients import create_redis_pools, close_redis_pools, get_redis_cache, get_redis_message_db
from redis.asyncio.client import Redis

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    log.info("Application startup...")
    try:
        await create_redis_pools()
        log.info("Redis pools initialized.")
    except Exception as e:
        log.critical(f"Application startup failed during Redis initialization: {e}")
        # Decide how to handle critical startup failure (e.g., exit)
        # For now, we'll let it proceed but Redis dependencies will fail
        pass # Or raise

    yield # Application runs here

    # --- Shutdown ---
    log.info("Application shutdown...")
    await close_redis_pools()
    log.info("Redis pools closed.")

# --- FastAPI App ---
app = FastAPI(
    lifespan=lifespan,
    docs_url = "/api/docs",
    redoc_url = "/api/redoc",
    openapi_url = "/api/openapi.json",
    title = "Iridium Message API",
    description = "API for processing Iridium messages.",
    version = "1.0.0",
)

# allow CORS for all origins
origins = ["*"]  # Change this to your frontend URL in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# add routes
from iridium.route import router as iridium_router
app.include_router(iridium_router, prefix="/api")


# --- API Endpoints ---
@app.get("/api/health", summary="Health Check")
async def health_check():
    """
    Health check endpoint to verify the service is running.
    """
    # Could add a Redis ping check here
    return {"status": "ok"}

@app.get("/api/manual/prediction/{payload_id}", summary="Trigger Flight Prediction Task")
async def force_prediction(
    request: Request,
    payload_id: str,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Force an update to a payload's predicted flight path by id.
    If the payload is not found, the request will be ignored.
    This is a manual trigger for the flight prediction task.
    The request is queued for asynchronous processing.
    """

    # get ip address from the request
    client_ip = x_forwarded_for if x_forwarded_for else request.client.host

    redis_data = {
        "sender": client_ip,
        "payload": payload_id,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Push the request onto the Redis queue
    queue_number = await message_queue.rpush("predict_flight", json.dumps(redis_data))

    # --- Return Response ---
    # should be accepted if the request is queued successfully
    if queue_number > 0:
        log.info(f"Request queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number}
    else:
        log.error("Failed to queue the request.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the request.",
        )

@app.get("/api/manual/path/{payload_id}", summary="Trigger Path Generation Task")
async def force_path_gen(
    request: Request,
    payload_id: str,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Force an update to a payload's path by id.
    If the payload is not found, the request will be ignored.
    This is a manual trigger for the path generation task.
    The request is queued for asynchronous processing.
    """

    # get ip address from the request
    client_ip = x_forwarded_for if x_forwarded_for else request.client.host

    redis_data = {
        "sender": client_ip,
        "payload": payload_id,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Push the request onto the Redis queue
    queue_number = await message_queue.rpush("get_path", json.dumps(redis_data))

    # --- Return Response ---
    # should be accepted if the request is queued successfully
    if queue_number > 0:
        log.info(f"Request queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number}
    else:
        log.error("Failed to queue the request.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the request.",
        )

@app.post("/api/manual/aprs", status_code=status.HTTP_202_ACCEPTED, summary="Upload raw APRS Message")
async def force_path_gen(
    request: Request,
    payload: str = Body(...),
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Manually upload a raw APRS message.
    This is a manual trigger for the APRS processing task.
    The request is queued for asynchronous processing.
    """

    # get ip address from the request
    client_ip = x_forwarded_for if x_forwarded_for else request.client.host

    redis_data = {
        "sender": client_ip,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Push the message onto the Redis queue
    queue_number = await message_queue.rpush("aprs", json.dumps(redis_data))

    # --- Return Response ---
    # should be accepted if the message is queued successfully
    if queue_number > 0:
        log.info(f"Message queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number}
    else:
        log.error("Failed to queue the message.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the message.",
        )

@app.post("/api/manual/lora", status_code=status.HTTP_202_ACCEPTED, summary="Upload raw LoRa Message")
async def force_path_gen(
    request: Request,
    payload: str = Body(...),
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Manually upload a raw LoRa message.
    This is a manual trigger for the LoRa processing task.
    The request is queued for asynchronous processing.
    """

    # get ip address from the request
    client_ip = x_forwarded_for if x_forwarded_for else request.client.host

    redis_data = {
        "sender": client_ip,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Push the message onto the Redis queue
    queue_number = await message_queue.rpush("lora", json.dumps(redis_data))

    # --- Return Response ---
    # should be accepted if the message is queued successfully
    if queue_number > 0:
        log.info(f"Message queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number}
    else:
        log.error("Failed to queue the message.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the message.",
        )