from fastapi import APIRouter, HTTPException, status, Depends, Header, Request

from utils import ip

from iridium.utils import verify_groundcontrol_jwt
from iridium.models import IridiumMessage

from redis_clients import get_redis_cache, get_redis_message_db
from redis.asyncio.client import Redis

from datetime import datetime, timezone
import json

router = APIRouter()

import logging
log = logging.getLogger(__name__)

@router.post(
    "/iridium",
    status_code=status.HTTP_202_ACCEPTED,  # Use 202 Accepted for queuing
    summary="Queue Iridium Message for Processing",
)
async def queue_iridium_message(
    message: IridiumMessage,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Receives an iridium message, validates it, and pushes it onto a
    Redis queue for asynchronous processing by the task handler service.
    """

    # 2. Verify the JWT signature from the validated message body
    try:
        # Pass the JWT string from the message to the verification function
        decoded_payload = verify_groundcontrol_jwt(message.JWT)
        log.info("JWT Verification Successful!")
        # Optionally use claims from decoded_payload if needed

    except HTTPException as auth_exception:
        # If verify_groundcontrol_jwt raised an HTTPException (e.g., 401), re-raise it
        raise auth_exception

    # --- Verification successful ---
    # If the code reaches here, the JSON structure is valid AND the JWT signature is verified.

    # Process the hex data (decode assuming UTF-8, adjust if different)
    try:
        decoded_data = bytes.fromhex(message.data).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as decode_error:
        log.error(f"Failed to decode hex data: {decode_error}")
        decoded_data = None

    # get ip address from the request
    client_ip = ip.get_ip(request, x_forwarded_for)
    log.info(f"Client IP: {client_ip}")
    log.info(f"Decoded Data: {decoded_data}")

    try:
        redis_data = {
            "sender": client_ip,
            "payload": message.dict(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        log.error(f"Failed to create redis_data dictionary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create redis_data dictionary.",
        )

    try:
        # Push the message onto the Redis queue (even if data decoding fails)
        queue_number = await message_queue.rpush("iridium", json.dumps(redis_data))
    except Exception as e:
        log.error(f"Failed to push message to Redis queue: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to push message to Redis queue.",
        )

    # --- Return Response ---
    # should be accepted if the message is queued successfully
    if queue_number > 0:
        log.info(f"Message queued successfully. Queue number: {queue_number}")
        return {"status": "queued", "queue_number": queue_number, "decode_success": decoded_data is not None}
    else:
        log.error("Failed to queue the message.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the message.",
        )