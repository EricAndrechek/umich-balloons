from fastapi import APIRouter, HTTPException, status, Depends, Header, Request

from utils import ip

from aprs.models import APRSMessage

from redis_clients import get_redis_cache, get_redis_message_db
from redis.asyncio.client import Redis

from datetime import datetime, timezone
import json

router = APIRouter()

import logging
log = logging.getLogger(__name__)

@router.post(
    "/aprs",
    status_code=status.HTTP_202_ACCEPTED,  # Use 202 Accepted for queuing
    summary="Queue APRS Message for Processing",
)
async def queue_aprs_message(
    message: APRSMessage,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
    message_queue: Redis = Depends(get_redis_message_db)
):
    """
    Receives an APRS message and pushes it onto a Redis queue for 
    asynchronous processing by the task handler service.
    """

    # TODO: handle message id caching/deduplication

    # get ip address from the request
    client_ip = ip.get_ip(request, x_forwarded_for)
    log.info(f"Client IP: {client_ip}")

    try:
        redis_data = {
            "sender": message.sender if message.sender else client_ip,
            "payload": message.model_dump(mode='json')["raw_data"],
            "timestamp": message.model_dump(mode='json')["timestamp"],
        }
    except Exception as e:
        log.error(f"Failed to create redis_data dictionary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create redis_data dictionary.",
        )

    try:
        # Push the message onto the Redis queue
        queue_number = await message_queue.rpush("aprs", json.dumps(redis_data))
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
        return {"status": "queued", "queue_number": queue_number}
    else:
        log.error("Failed to queue the message.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue the message.",
        )