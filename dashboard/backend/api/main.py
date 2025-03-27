import os
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel, Field, field_validator

from jose import jwt, JWTError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from datetime import datetime  # Optional: for parsing transmit_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
GROUND_CONTROL_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAlaWAVJfNWC4XfnRx96p9
cztBcdQV6l8aKmzAlZdpEcQR6MSPzlgvihaUHNJgKm8t5ShR3jcDXIOI7er30cIN
4/9aVFMe0LWZClUGgCSLc3rrMD4FzgOJ4ibD8scVyER/sirRzf5/dswJedEiMte1
ElMQy2M6IWBACry9u12kIqG0HrhaQOzc6Tr8pHUWTKft3xwGpxCkV+K1N+9HCKFc
cbwb8okRP6FFAMm5sBbw4yAu39IVvcSL43Tucaa79FzOmfGs5mMvQfvO1ua7cOLK
fAwkhxEjirC0/RYX7Wio5yL6jmykAHJqFG2HT0uyjjrQWMtoGgwv9cIcI7xbsDX6
owIDAQAB
-----END PUBLIC KEY-----"""

try:
    public_key = serialization.load_pem_public_key(
        GROUND_CONTROL_PUBLIC_KEY_PEM.encode("utf-8"), backend=default_backend()
    )
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError("Key is not an RSA public key")
except Exception as e:
    logger.error(f"Error loading public key: {e}")
    raise SystemExit("Failed to load critical public key.")

ALGORITHMS = ["RS256"]  # Assuming RS256, verify if different

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
IRIDIUM_QUEUE_NAME = "iridium_message_queue"

# --- Redis Setup ---
redis_pool = redis.ConnectionPool.from_url(
    REDIS_URL, decode_responses=False
)  # Keep bytes for task queue? Or True? Let's use True for JSON string.
redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


async def get_redis() -> redis.Redis:
    """Dependency to get an async Redis connection."""
    async with redis.Redis(connection_pool=redis_pool) as r:
        yield r


# --- Lifespan Management (Simpler) ---
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("FastAPI startup...")
    # Check Redis connection
    r = await get_redis().__anext__()
    try:
        await r.ping()
        logger.info("Redis connection successful.")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        # Decide if you want to prevent startup if Redis is down
        # raise RuntimeError("Failed to connect to Redis") from e

    yield  # Application runs here

    logger.info("FastAPI shutdown...")
    await redis_pool.disconnect()
    logger.info("Redis pool disconnected.")


# --- FastAPI App ---
app = FastAPI(lifespan=lifespan)

# change fastapi docs to use /api prefix
app.docs_url = "/api/docs"
app.redoc_url = "/api/redoc"
app.openapi_url = "/api/openapi.json"
app.title = "Iridium Message API"
app.description = "API for processing Iridium messages."
app.version = "1.0.0"

# --- API Endpoints ---
@app.get("/api/health", summary="Health Check")
async def health_check():
    # Could add a Redis ping check here
    return {"status": "ok"}

class IridiumMessage(BaseModel):
    """Model for Iridium message."""
    momsn: int = Field(..., description="Message ID")
    imei: str = Field(..., description="IMEI of the device")
    data: str = Field(..., description="Message data as a hex string")
    serial: int = Field(..., description="Serial number of the device")
    device_type: str = Field(..., description="Type of device")
    iridium_latitude: float = Field(..., description="Latitude in degrees")
    iridium_longitude: float = Field(..., description="Longitude in degrees")
    iridium_cep: float = Field(..., description="CEP in meters")
    transmit_time: str = Field(..., description="Transmit time like 25-03-26 23:45:44")
    JWT: str = Field(..., description="JWT token for authentication")


# --- Helper function for JWT Verification ---
def verify_groundcontrol_jwt(jwt_token: str):
    """
    Verifies the JWT signature using Ground Control's public key.
    Raises HTTPException if verification fails.
    Returns the decoded payload upon success.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate JWT signature",
    )
    try:
        # Decode and verify the JWT
        payload = jwt.decode(
            jwt_token,
            public_key,
            algorithms=ALGORITHMS,
            # Optional: Add audience/issuer validation if applicable
            # options={"verify_aud": False, "verify_iss": False}
        )
        # --- Optional: Verify claims within the JWT ---
        # Example: Check if 'data' claim in JWT matches 'data' in outer JSON
        # jwt_data_claim = payload.get("data")
        # if jwt_data_claim is None or jwt_data_claim != message.data: # Need access to message here
        #      raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JWT data claim mismatch")

        # You could add more claim validations here (e.g., issuer, imei)

    except JWTError as e:
        print(f"JWT Validation Error: {e}")  # Log for debugging
        raise credentials_exception from e  # Raise the HTTP exception

    return payload  # Return decoded payload if verification is successful


@app.post(
    "/api/rock7-upload",
    status_code=status.HTTP_202_ACCEPTED,  # Use 202 Accepted for queuing
    summary="Queue Iridium Message for Processing",
)
async def queue_iridium_message(
    message: IridiumMessage, redis_client: redis.Redis = Depends(get_redis)
):
    """
    Receives an iridium message, validates it, and pushes it onto a
    Redis queue for asynchronous processing by the task handler service.
    """

    # 2. Verify the JWT signature from the validated message body
    try:
        # Pass the JWT string from the message to the verification function
        decoded_payload = verify_groundcontrol_jwt(message.JWT)
        print("JWT Verification Successful!")
        # Optionally use claims from decoded_payload if needed

    except HTTPException as auth_exception:
        # If verify_groundcontrol_jwt raised an HTTPException (e.g., 401), re-raise it
        raise auth_exception

    # --- Verification successful ---
    # If the code reaches here, the JSON structure is valid AND the JWT signature is verified.
    print(f"Received verified message for IMEI: {message.imei}")
    print(f"MOMSN: {message.momsn}, Serial: {message.serial}")
    print(f"Transmit Time: {message.transmit_time}")
    print(
        f"Location: ({message.iridium_latitude}, {message.iridium_longitude}), CEP: {message.iridium_cep}"
    )

    # Process the hex data (decode assuming UTF-8, adjust if different)
    try:
        decoded_data = bytes.fromhex(message.data).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as decode_error:
        logger.error(f"Failed to decode hex data: {decode_error}")
        decoded_data = None

    # Push the message onto the Redis queue (even if data decoding fails)
    queue_number = await redis_client.rpush(IRIDIUM_QUEUE_NAME, message.model_dump_json())

    # --- Return Response ---
    # should be accepted if the message is queued successfully
    if queue_number > 0:
        logger.info(f"Message queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number, "decode_success": decoded_data is not None}
    else:
        logger.error("Failed to queue the message.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the message.",
        )
