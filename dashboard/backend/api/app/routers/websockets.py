from fastapi import FastAPI, WebSocket, WebSocketDisconnect, APIRouter, HTTPException, status, Depends, Header, Request
import redis.asyncio as redis

from ..utils import network, grid
from ..services.connection_manager import manager
from ..core.redis_client import get_redis
from ..core import database
from ..models import models

from datetime import datetime, timezone
import json

router = APIRouter()

import logging

log = logging.getLogger(__name__)

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    redis: redis.Redis = Depends(get_redis)
):
    await manager.connect(websocket)
    try:
        while True:
            # Receive message from client
            raw_data = await websocket.receive_text()
            try:
                message = json.loads(raw_data)
                msg_type = message.get("type")
                payload = message.get("payload", {})
                request_id = message.get(
                    "request_id"
                )  # Optional ID for client tracking

                response = models.WebSocketResponse(
                    type="unknownResponse", request_id=request_id
                )  # Default response structure

                # --- Handle different message types ---
                if msg_type == "getInitialData":
                    try:
                        request = models.InitialDataRequest(**payload)
                        current_cells = grid.get_cells_for_bbox(request.bbox)

                        async with database.get_db_connection() as db:
                            historical_data = await fetch_historical_data(
                                db, request.bbox, request.historyDuration
                            )

                        await manager.update_subscriptions(websocket, current_cells)

                        response.type = "initialData"
                        response.data = [
                            path.model_dump() for path in historical_data
                        ]  # Send serializable data
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                        # Optionally confirm subscription separately
                        # await manager.send_personal_message(json.dumps({"type": "subscriptionUpdate", "cells": list(current_cells)}), websocket)

                    except Exception as e:
                        log.error(f"Error processing getInitialData: {e}")
                        response.type = "error"
                        response.error = f"Failed to get initial data: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                elif msg_type == "updateViewport":
                    try:
                        request = models.UpdateViewportRequest(**payload)
                        new_cells = grid.get_cells_for_bbox(request.bbox)
                        cells_to_join, _ = await manager.update_subscriptions(
                            websocket, new_cells
                        )

                        # Fetch catch-up data only if new cells were joined
                        if cells_to_join:
                            async with database.get_db_connection() as db:
                                # Query using the new bbox for simplicity, could optimize query area
                                catch_up_data = await fetch_historical_data(
                                    db, request.bbox, "3 hours"
                                )  # Adjust duration?

                            response.type = "catchUpData"
                            response.data = [
                                path.model_dump() for path in catch_up_data
                            ]
                            await manager.send_personal_message(
                                response.model_dump_json(), websocket
                            )
                        # Acknowledge viewport update even if no new data
                        # elif request_id: # Send empty success if needed
                        #      response.type = "viewportUpdated"
                        #      await manager.send_personal_message(response.model_dump_json(), websocket)

                    except Exception as e:
                        log.error(f"Error processing updateViewport: {e}")
                        response.type = "error"
                        response.error = f"Failed to update viewport: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                elif msg_type == "getTelemetry":
                    try:
                        request = models.TelemetryRequest(**payload)
                        cache_key = f"telemetry:{request.payloadId}:{request.timestamp}"
                        cached_data = await redis.get(cache_key)
                        telemetry_data_dict = None

                        if cached_data:
                            log.debug(f"Telemetry cache hit for {cache_key}")
                            telemetry_data_dict = json.loads(cached_data)
                        else:
                            log.debug(f"Telemetry cache miss for {cache_key}, querying DB.")
                            async with database.get_db_connection() as db:
                                telemetry_model = await fetch_telemetry_data(
                                    db, request.payloadId, request.timestamp
                                )
                                if telemetry_model:
                                    telemetry_data_dict = telemetry_model.model_dump()
                                    await redis.set(
                                        cache_key,
                                        json.dumps(telemetry_data_dict),
                                        ex=3600,
                                    )  # Cache 1hr

                        response.type = "telemetryResponse"
                        response.data = {
                            "payloadId": request.payloadId,
                            "timestamp": request.timestamp,
                            "telemetry": telemetry_data_dict,
                        }
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                    except Exception as e:
                        log.error(f"Error processing getTelemetry: {e}")
                        response.type = "error"
                        response.error = f"Failed to get telemetry: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                else:
                    # Handle unknown message type
                    log.info(f"Received unknown message type: {msg_type}")
                    response.type = "error"
                    response.error = f"Unknown message type: {msg_type}"
                    await manager.send_personal_message(
                        response.model_dump_json(), websocket
                    )

            except json.JSONDecodeError:
                log.info("Received invalid JSON over WebSocket")
                await manager.send_personal_message(
                    json.dumps({"type": "error", "error": "Invalid JSON format."}),
                    websocket,
                )
            except Exception as e:
                log.error(f"Error processing WebSocket message: {e}")
                try:
                    await manager.send_personal_message(
                        json.dumps(
                            {"type": "error", "error": f"Internal server error: {e}"}
                        ),
                        websocket,
                    )
                except:
                    pass  # Ignore if sending error fails (socket likely closed)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(f"Client left the chat")
    except Exception as e:
        # Catch unexpected errors during the connection loop
        log.error(f"Unexpected error for websocket {websocket.client.host}: {e}")
        manager.disconnect(websocket)
