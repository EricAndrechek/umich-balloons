# app/routers/ingress.py
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
import redis.asyncio as redis
from datetime import datetime, timezone
from typing import Dict, Any

# --- Utility and Core Imports ---
from ..utils import network
from ..core.redis_client import get_redis
from ..utils.security import verify_groundcontrol_jwt
from ..models.models import (
    APRSMessage,
    LoRaMessage,
    IridiumMessage,
    QueueStatusResponse,
)

from aprspy import APRS

# --- Router Setup ---
router = APIRouter(
    tags=["Ingress"],
)
log = logging.getLogger(__name__)

# --- Helper Function for Redis Push ---

async def _push_to_redis_queue(
    redis_client: redis.Redis, queue_name: str, data: Dict[str, Any]
) -> int:
    """
    Serializes data and pushes it to a named Redis list using RPUSH.
    Handles common errors and returns the new queue length on success.
    Raises HTTPException on failure.
    """
    try:
        redis_message_json = json.dumps(data)
        log.debug(
            f"Attempting to push to Redis queue '{queue_name}': {redis_message_json[:150]}..."
        )  # Log snippet
    except Exception as e:
        log.error(
            f"Failed to serialize data for Redis queue '{queue_name}': {e}",
            exc_info=True,
        )
        # Raise HTTPException directly here as it's an internal server error condition
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to serialize data for queue '{queue_name}': {e}",
        ) from e

    try:
        queue_length = await redis_client.rpush(queue_name, redis_message_json)
        log.info(
            f"Message pushed to '{queue_name}' Redis list. New length: {queue_length}"
        )
        if not isinstance(queue_length, int) or queue_length <= 0:
            # RPUSH should return int > 0 on success for non-empty pushes
            log.error(
                f"Redis RPUSH to '{queue_name}' returned unexpected value: {queue_length}"
            )
            raise ValueError("Redis RPUSH command did not indicate success.")
        return queue_length
    except redis.RedisError as e:
        log.error(f"Redis error pushing to '{queue_name}' list: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue message (Redis Error): {e}",
        ) from e
    except Exception as e:  # Includes the ValueError from above or other issues
        log.error(
            f"Unexpected error pushing to '{queue_name}' list: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error queuing message: {e}",
        ) from e


# --- APRS Ingress Route ---


@router.post(
    "/aprs",
    response_model=QueueStatusResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue APRS Message for Processing",
)
async def queue_aprs_message(
    message: APRSMessage,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: redis.Redis = Depends(get_redis),
):
    """
    Receives an APRS message via POST, extracts info,
    and pushes it onto the 'aprs' Redis list for async processing.
    """
    log.info("Received request on /aprs")
    client_ip = network.get_ip(request, x_forwarded_for)
    log.debug(f"APRS Client IP: {client_ip}")

    decoded_data = None
    # 1. Decode the APRS message (if string and not JSON/object)
    if isinstance(message.raw_data, str):
        try:
            decoded_data = APRS.parse(message.raw_data)
            destination = decoded_data.destination
            source = decoded_data.source
            log.info(f"Successfully decoded APRS data from {source} to {destination}.")
        except Exception as decode_error:
            decoded_data = False
            log.warning(f"Failed to decode APRS data: {decode_error}. Proceeding.")
            # Keep message.data as is
    else:
        log.info("Received APRS data is not a string, skipping decoding.")

    # Prepare data specifically for APRS queue
    try:
        message_dict = message.model_dump(mode="json")
        redis_data = {
            "sender": message.sender if message.sender else client_ip,
            "payload": message_dict.get("raw_data"),
            "timestamp": message_dict.get("timestamp"),
            "ingest_method": "HTTP",
        }
    except Exception as e:
        log.error(f"Failed to create APRS redis_data dictionary: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to prepare APRS data for queue: {e}",
        ) from e

    # Call the common helper function to push to the 'aprs' queue
    queue_length = await _push_to_redis_queue(
        redis_client=message_queue, queue_name="aprs", data=redis_data
    )

    # if decoded_data is None, omit the field (so pass None into the response)
    # if decoded_data is False, set the field to False
    # if decoded_data is something else, set the field to True
    if decoded_data is not None:
        decoded_data = True if decoded_data else False

    return QueueStatusResponse(
        queue_number=queue_length,
        decode_success=decoded_data,
    )


# --- LoRa Ingress Route ---


@router.post(
    "/lora",
    response_model=QueueStatusResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue LoRa Message for Processing",
)
async def queue_lora_message(
    message: LoRaMessage,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: redis.Redis = Depends(get_redis),
):
    """
    Receives a LoRa message via POST, extracts info,
    and pushes it onto the 'lora' Redis list for async processing.
    """
    log.info("Received request on /lora")
    client_ip = network.get_ip(request, x_forwarded_for)
    log.debug(f"LoRa Client IP: {client_ip}")

    # Prepare data specifically for LoRa queue
    try:
        message_dict = message.model_dump(mode="json")
        redis_data = {
            "sender": message.sender if message.sender else client_ip,
            "payload": message_dict.get("raw_data"),
            "timestamp": message_dict.get("timestamp"),
            "ingest_method": "HTTP",
        }
    except Exception as e:
        log.error(f"Failed to create LoRa redis_data dictionary: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to prepare LoRa data for queue: {e}",
        ) from e

    # Call the common helper function to push to the 'lora' queue
    queue_length = await _push_to_redis_queue(
        redis_client=message_queue, queue_name="lora", data=redis_data
    )

    return QueueStatusResponse(queue_number=queue_length)


# --- Iridium Ingress Route ---


@router.post(
    "/iridium",
    response_model=QueueStatusResponse,  # Use common response model
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue Iridium Message for Processing",
)
async def queue_iridium_message(
    message: IridiumMessage,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: redis.Redis = Depends(get_redis),
):
    """
    Receives an Iridium message via POST, validates JWT, extracts info,
    decodes hex data, and pushes it onto the 'iridium' Redis list.
    """
    log.info("Received request on /ingress/iridium")

    # 1. Verify JWT (Specific to Iridium)
    try:
        decoded_payload = verify_groundcontrol_jwt(message.JWT)
        log.info("JWT Verification Successful!")
        log.debug(f"Decoded JWT Payload: {decoded_payload}")
    except HTTPException as auth_exception:
        log.warning(f"JWT Verification Failed: {auth_exception.detail}")
        raise auth_exception
    except Exception as e:
        log.error(f"Unexpected error during JWT verification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during JWT verification: {e}",
        ) from e

    # 2. Decode Hex Data (Specific to Iridium)
    decoded_data = None
    try:
        decoded_data = bytes.fromhex(message.data).decode("utf-8")
        log.info("Successfully decoded hex data.")
    except (ValueError, UnicodeDecodeError) as decode_error:
        log.warning(f"Failed to decode hex data: {decode_error}. Proceeding.")
        # Keep decoded_data as None

    # 3. Get client IP address
    client_ip = network.get_ip(request, x_forwarded_for)
    log.debug(f"Iridium Client IP: {client_ip}")

    # 4. Prepare data specifically for Iridium queue
    try:
        message_dict = message.model_dump(mode="json")
        redis_data = {
            "sender": client_ip,
            "payload": message_dict,
            "timestamp": message_dict.get("transmit_time"),
        }
    except Exception as e:
        log.error(f"Failed to create Iridium redis_data dictionary: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to prepare Iridium data for queue: {e}",
        ) from e

    # 5. Call the common helper function to push to the 'iridium' queue
    queue_length = await _push_to_redis_queue(
        redis_client=message_queue, queue_name="iridium", data=redis_data
    )

    # Return common response structure, adding specific fields if needed
    return QueueStatusResponse(
        queue_number=queue_length,
        decode_success=(decoded_data is not None),  # Specific to Iridium response
    )
