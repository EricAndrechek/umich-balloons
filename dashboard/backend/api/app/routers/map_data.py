import re
from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
    status,
)
import redis.asyncio as redis
import logging
from datetime import timedelta

import json
import asyncpg
from typing import List, Dict, Any, Optional, Union

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone, time as dt_time
from dateutil.relativedelta import relativedelta

from ..models import models
from ..core import database, redis_client

from ..services.connection_manager import manager  # Import the manager instance
from ..core.config import settings

log = logging.getLogger(__name__)

router = APIRouter()

# --- WebSocket Handlers (within router context) ---

async def handle_get_initial_data(
    websocket: WebSocket, payload: dict, request_id: Optional[str]
):
    """Handles the 'getInitialData' message from a client."""
    response = models.WebSocketResponse(
        type="error", request_id=request_id
    )  # Default to error
    try:
        request_model = models.InitialDataRequest(**payload)
        # convert set to list for JSON serialization
        history_hours = request_model.history_seconds / 3600

        # Calculate time range (ensure UTC)
        end_time_utc = datetime.now(timezone.utc)
        start_time_utc = end_time_utc - timedelta(hours=history_hours)

        # --- Caching/Database Call ---
        # Call the new function which handles caching internally
        # This function is async native because of Redis calls, no need for to_thread here
        # (though db calls *within* it still use to_thread)
        try:
            initial_path_data = await database.get_historical_paths_with_cache_async(
                request_model.geohashes, start_time_utc, end_time_utc
            )
            response.type = "initialPathSegments"  # Type matches data structure being sent
            response.data = initial_path_data  # Should already be jsonb format?
        except Exception as e:
            # Catch potential errors from the caching function itself (e.g., key generation fail)
            log.error(f"Error getting historical paths (with cache): {e}", exc_info=True)
            initial_path_data = [] # Default to empty on error

            response.type = "error"
            response.error = f"Failed to get initial path data: {getattr(e, 'detail', str(e))}"  # Get detail if HTTPException
        # --- End Caching/Database Call ---

        # subscribe client to geohashes
        await manager.update_subscriptions(websocket, request_model.geohashes)

    except Exception as e:
        log.error(
            f"Error processing getInitialData for {websocket.client.host}: {e}",
            exc_info=True,
        )
        response.type = "error"
        response.error = f"Failed to get initial path data: {getattr(e, 'detail', str(e))}"  # Get detail if HTTPException

    # Always try to send a response (success or error)
    await manager.send_personal_message(response.model_dump_json(), websocket)


async def handle_update_viewport(
    websocket: WebSocket, payload: dict, request_id: Optional[str]
):
    """Handles the 'updateViewport' message."""
    response = models.WebSocketResponse(type="error", request_id=request_id)
    try:
        request_model = models.UpdateViewportRequest(**payload)
        joined_hashes, left_hashes = await manager.update_subscriptions(websocket, request_model.geohashes)

        if joined_hashes:
            log.info(
                f"Socket joined {len(joined_hashes)} cells. Fetching catch-up path segments."
            )
            try:
                # default to 3 hours if not specified
                history_hours = 3

                # Calculate time range (ensure UTC)
                end_time_utc = datetime.now(timezone.utc)
                start_time_utc = end_time_utc - timedelta(hours=history_hours)
                catchup_path_data = (
                    await database.get_historical_paths_with_cache_async(
                        list(joined_hashes), start_time_utc, end_time_utc
                    )
                )
                # Fetch points too if using hybrid approach

                response.type = "catchUpPathSegments"  # Type matches data
                response.data = catchup_path_data
            except Exception as e:
                log.error(f"Error getting catch-up path data: {e}", exc_info=True)
                response.type = "error"
                response.error = f"Failed to get catch-up path data: {getattr(e, 'detail', str(e))}"

        else: # Optionally send an ack even if no new cells/data joined
           response.type = "viewportUpdated"

    except Exception as e:
        log.error(
            f"Error processing updateViewport for {websocket.client.host}: {e}",
            exc_info=True,
        )
        response.type = "error"
        response.error = f"Failed to update viewport: {getattr(e, 'detail', str(e))}"

    await manager.send_personal_message(response.model_dump_json(), websocket)

async def handle_get_balloon_details(
    websocket: WebSocket, payload: dict, request_id: Optional[str]
):
    """Handles the 'getBalloonDetails' message."""
    response = models.WebSocketResponse(type="error", request_id=request_id)
    try:
        request_model = models.NameAndSymbolRequest(**payload)
        # message will have a payloadId,
        # we should return the balloon name and symbol

        response.type = "balloonDetailsResponse"

        name_symbol_data = await database.get_name_and_symbol(str(request_model.payload_id))
        if not name_symbol_data:
            response.type = "error"
            response.error = f"Payload ID {request_model.payload_id} not found."

        else:
            name_symbol_data["payload_id"] = str(request_model.payload_id)
            response.data = name_symbol_data

    except Exception as e:
        log.error(
            f"Error processing updateViewport for {websocket.client.host}: {e}",
            exc_info=True,
        )
        response.type = "error"
        response.error = f"Failed to get payload: {getattr(e, 'detail', str(e))}"

    await manager.send_personal_message(response.model_dump_json(), websocket)


async def handle_get_telemetry(
    websocket: WebSocket, payload: dict, request_id: Optional[str]
):
    pass


# --- WebSocket Endpoint ---

@router.websocket("/ws")  # Ensure path is correct
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                message = json.loads(raw_data)
                msg_type = message.get("type")
                payload = message.get("payload", {})
                request_id = message.get("request_id")

                log.info(
                    f"WebSocket {websocket.client.host} received: Type={msg_type}, ReqID={request_id}"
                )

                # --- Delegate to handlers ---
                if msg_type == "getInitialData":
                    await handle_get_initial_data(websocket, payload, request_id)
                elif msg_type == "updateViewport":
                    await handle_update_viewport(websocket, payload, request_id)
                elif msg_type == "getTelemetry":
                    # Only call if you re-added telemetry fetching
                    # await handle_get_telemetry(websocket, payload, request_id)
                    log.warning(
                        f"Received getTelemetry request, but handler is disabled/removed."
                    )
                    # Optionally send error back
                    response = models.WebSocketResponse(
                        type="error",
                        request_id=request_id,
                        error="Telemetry fetching not currently enabled.",
                    )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )
                    pass  # Keep commented out if telemetry not needed
                elif msg_type == "getBalloonDetails":
                    await handle_get_balloon_details(websocket, payload, request_id)
                elif msg_type == "startRaw":
                    # Handle startRaw message
                    log.info(
                        f"WebSocket {websocket.client.host} requested startRaw."
                    )
                    # Optionally send a response back
                    response = models.WebSocketResponse(
                        type="rawStarted",
                        request_id=request_id,
                        data="Raw data streaming started.",
                    )
                    await manager.add_to_raw(
                        websocket
                    )
                elif msg_type == "stopRaw":
                    # Handle stopRaw message
                    log.info(
                        f"WebSocket {websocket.client.host} requested stopRaw."
                    )
                    # Optionally send a response back
                    response = models.WebSocketResponse(
                        type="rawStopped",
                        request_id=request_id,
                        data="Raw data streaming stopped.",
                    )
                    await manager.remove_from_raw(
                        websocket
                    )
                elif msg_type == "wsStats":
                    # Handle wsStats message
                    log.info(
                        f"WebSocket {websocket.client.host} requested server stats."
                    )
                    # Optionally send a response back
                    response = models.WebSocketResponse(
                        type="wsStats",
                        request_id=request_id,
                        data={
                            "active_connections": len(manager.active_connections),
                            "rooms": list(manager.room_connections.keys()),
                        },
                    )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )
                elif msg_type == "ping":
                    # Handle ping message
                    log.info(
                        f"WebSocket {websocket.client.host} sent ping."
                    )
                    # Optionally send a response back
                    response = models.WebSocketResponse(
                        type="pong",
                        request_id=request_id,
                        data="pong",
                    )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )
                elif msg_type == "getTelem":
                    # get payload
                    payload_id = payload.get("payload_id")
                    if not payload_id:
                        raise ValueError("Payload ID is required for getTelem.")
                    # Call the telemetry function
                    telemetry_data = await database.get_telemetry(payload_id)
                    if telemetry_data:
                        response = models.WebSocketResponse(
                            type="telemetryData",
                            request_id=request_id,
                            data=telemetry_data,
                        )
                    else:
                        response = models.WebSocketResponse(
                            type="error",
                            request_id=request_id,
                            error=f"No telemetry data found for payload ID {payload_id}.",
                        )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )
                else:
                    # Handle unknown type
                    log.warning(
                        f"Received unknown WebSocket message type: {msg_type} from {websocket.client.host}"
                    )
                    response = models.WebSocketResponse(
                        type="error",
                        request_id=request_id,
                        error=f"Unknown message type: {msg_type}",
                    )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )

            except json.JSONDecodeError:
                log.warning(
                    f"Received invalid JSON over WebSocket from {websocket.client.host}"
                )
                await manager.send_personal_message(
                    json.dumps({"type": "error", "error": "Invalid JSON format."}),
                    websocket,
                )
            except Exception as e:
                # Catch errors from within the handlers if they don't send error msg themselves
                log.error(
                    f"Error processing WebSocket message for {websocket.client.host}: {e}",
                    exc_info=True,
                )
                try:
                    # Send generic error if specific handler didn't
                    response = models.WebSocketResponse(
                        type="error",
                        request_id=message.get("request_id"),
                        error=f"Internal server error processing message: {e}",
                    )
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )
                except:
                    log.error(
                        f"Error sending error response to WebSocket {websocket.client.host}: {e}",
                        exc_info=True,
                    )
                    pass  # Ignore if sending fails now
                # Decide if loop should break on handler error? Maybe not, allow client to continue
                # break

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {websocket.client.host}")
    except Exception as e:
        log.error(
            f"Unexpected error in WebSocket connection for {websocket.client.host}: {e}",
            exc_info=True,
        )
    finally:
        # Ensure disconnect cleanup happens
        manager.disconnect(websocket)
