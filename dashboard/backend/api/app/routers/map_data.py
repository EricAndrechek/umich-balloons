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

from ..models import models
from ..utils import grid
from ..core import database, redis_client

from ..services.connection_manager import manager  # Import the manager instance
from ..core.config import settings

log = logging.getLogger(__name__)

router = APIRouter()

# --- Helper Functions for DB Queries (within router context) ---


async def fetch_historical_data(
    db: asyncpg.Connection, bbox: models.Bbox, history_seconds: int
) -> models.GeoJsonFeatureCollection:
    """
    Queries the 'mv_payload_path_segments' materialized view for recent path segments
    intersecting the bbox and within the history window. Returns a GeoJSON FeatureCollection.
    """
    query = """
        SELECT
            mv.payload_id,
            mv.segment_start_time, -- For potential properties
            mv.segment_end_time,   -- For potential properties
            -- Convert path_segment geography directly to GeoJSON geometry object string
            ST_AsGeoJSON(mv.path_segment) AS segment_geojson_geom
        FROM
            public.mv_payload_path_segments mv
        WHERE
            -- Filter segments whose time range overlaps the requested history window
            -- Assumes segment_start_time/end_time cover the bucket accurately
            TSTZRANGE(mv.segment_start_time, mv.segment_end_time, '[]') &&
            TSTZRANGE(NOW() AT TIME ZONE 'utc' - $1::interval, NOW() AT TIME ZONE 'utc', '[]')

            -- Spatial intersection filter
            AND ST_Intersects(
                    mv.path_segment,
                    ST_MakeEnvelope($2, $3, $4, $5, 4326)::geography
                );
    """
    try:
        interval_timedelta = timedelta(seconds=history_seconds)
        log.debug(
            f"Fetching historical path segments with interval '{interval_timedelta}' and bbox {bbox.model_dump()}"
        )

        rows = await db.fetch(
            query,
            interval_timedelta,
            bbox.minLon,
            bbox.minLat,
            bbox.maxLon,
            bbox.maxLat,
        )
        log.debug(f"Found {len(rows)} path segments intersecting viewport.")

        # --- Convert rows to GeoJSON Feature Collection ---
        features = []
        for row in rows:
            try:
                # ST_AsGeoJSON returns a JSON string, parse it
                geometry_dict = json.loads(row["segment_geojson_geom"])
                feature = models.GeoJsonFeature(
                    properties=models.GeoJsonProperties(
                        payload_id=row["payload_id"],
                        # Optionally format timestamps if needed
                        # start_time=row['segment_start_time'].isoformat(),
                        # end_time=row['segment_end_time'].isoformat()
                    ),
                    geometry=models.GeoJsonGeometry(
                        coordinates=geometry_dict.get("coordinates", [])
                    ),
                )
                features.append(feature)
            except (json.JSONDecodeError, TypeError, KeyError) as parse_error:
                log.error(
                    f"Error parsing GeoJSON segment for payload_id {row.get('payload_id', 'N/A')}: {parse_error}",
                    exc_info=True,
                )
                continue  # Skip this segment if parsing fails

        return models.GeoJsonFeatureCollection(features=features)
    except asyncpg.exceptions.DataError as e:
        log.error(
            f"Database DataError fetching historical segments: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Database query data error: {e}") from e
    except Exception as e:
        log.error(f"Error fetching historical data in router: {e}", exc_info=True)
        # Depending on desired behavior, either raise or return empty/error indicator
        raise HTTPException(
            status_code=500, detail=f"Database error fetching history: {e}"
        ) from e


async def fetch_telemetry_data(
    db: asyncpg.Connection, payload_id: int, timestamp: str
) -> models.TelemetryData | None:
    """Queries the 'telemetry' table for specific data point details."""
    query = """
        SELECT altitude, speed, course, battery, accuracy, extra
        FROM public.telemetry
        WHERE payload_id = $1 AND data_time = $2::timestamptz
        LIMIT 1;
    """
    try:
        log.debug(
            f"Fetching telemetry for payload_id={payload_id} at timestamp={timestamp}"
        )
        # Ensure timestamp is in a format Postgres understands, ISO 8601 is good.
        row = await db.fetchrow(query, payload_id, timestamp)
        if row:
            log.debug(f"Found telemetry data: {dict(row)}")
            # Directly create Pydantic model from row (column names match model fields)
            return models.TelemetryData(**dict(row))
        log.debug("No telemetry data found.")
        return None
    except Exception as e:
        log.error(f"Error fetching telemetry data in router: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Database error fetching telemetry: {e}"
        )


# --- WebSocket Endpoint (Logic Mostly Unchanged, Uses Adapted Helpers) ---


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    redis = await redis_client.get_redis()  # Get redis client instance

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

                log.debug(
                    f"WebSocket {websocket.client.host} received: Type={msg_type}, ReqID={request_id}"
                )

                response = models.WebSocketResponse(
                    type="unknownResponse", request_id=request_id
                )  # Default response structure

                # --- Handle different message types ---
                if msg_type == "getInitialData":
                    try:
                        request_model = models.InitialDataRequest(**payload)
                        current_cells = grid.get_cells_for_bbox(request_model.bbox)

                        async with database.get_db_connection() as db:
                            # Calls the *updated* function which now returns GeoJSON FeatureCollection
                            historical_data_geojson = await fetch_historical_data(
                                db, request_model.bbox, request_model.history_seconds
                            )

                        await manager.update_subscriptions(websocket, current_cells)

                        response.type = (
                            "initialPathSegments"  # Use a new type name for clarity
                        )
                        response.data = (
                            historical_data_geojson.model_dump()
                        )  # Send the whole FeatureCollection
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )
                        log.info(
                            f"Sent initialPathSegments to {websocket.client.host} with {len(historical_data_geojson.features)} segments."
                        )

                    except Exception as e:
                        log.error(
                            f"Error processing getInitialData for {websocket.client.host}: {e}",
                            exc_info=True,
                        )
                        response.type = "error"
                        response.error = f"Failed to get initial path data: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                elif msg_type == "updateViewport":
                    # NOTE: This also needs adjustment if you want 'catchUpData' to send segments
                    # For now, it would refetch using the same logic as getInitialData
                    try:
                        request_model = models.UpdateViewportRequest(**payload)
                        new_cells = grid.get_cells_for_bbox(request_model.bbox)
                        cells_to_join, _ = await manager.update_subscriptions(
                            websocket, new_cells
                        )

                        if cells_to_join:
                            log.debug(
                                f"Socket {websocket.client.host} joined {len(cells_to_join)} cells. Fetching catch-up path segments."
                            )
                            async with database.get_db_connection() as db:
                                # Use the adapted fetch_historical_data function
                                catch_up_data_geojson = await fetch_historical_data(
                                    db,
                                    request_model.bbox,
                                    10800,  # Use a relevant duration for catch-up (e.g., 3 hours default)
                                )

                            response.type = "catchUpPathSegments"  # Use a new type name
                            response.data = catch_up_data_geojson.model_dump()
                            await manager.send_personal_message(
                                response.model_dump_json(), websocket
                            )
                            log.info(
                                f"Sent catchUpPathSegments to {websocket.client.host} with {len(catch_up_data_geojson.features)} segments."
                            )

                    except Exception as e:
                        log.error(
                            f"Error processing updateViewport for {websocket.client.host}: {e}",
                            exc_info=True,
                        )
                        response.type = "error"
                        response.error = f"Failed to update viewport: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                elif msg_type == "getTelemetry":
                    try:
                        request_model = models.TelemetryRequest(**payload)
                        cache_key = f"telemetry:{request_model.payloadId}:{request_model.timestamp}"
                        telemetry_data_dict = None  # Initialize

                        # 1. Check Redis Cache
                        cached_data = await redis.get(cache_key)
                        if cached_data:
                            log.debug(f"Telemetry cache hit for {cache_key}")
                            telemetry_data_dict = json.loads(cached_data)
                        else:
                            # 2. Cache Miss - Query Database
                            log.debug(
                                f"Telemetry cache miss for {cache_key}, querying DB."
                            )
                            async with database.get_db_connection() as db:
                                # Use the adapted fetch_telemetry_data function
                                telemetry_model = await fetch_telemetry_data(
                                    db, request_model.payloadId, request_model.timestamp
                                )
                                if telemetry_model:
                                    # Convert model to dict for caching/sending
                                    telemetry_data_dict = telemetry_model.model_dump()
                                    # 3. Cache Result
                                    await redis.set(
                                        cache_key,
                                        json.dumps(telemetry_data_dict),
                                        ex=3600,
                                    )  # Cache 1hr

                        # 4. Send Response
                        response.type = "telemetryResponse"
                        response.data = {
                            "payloadId": request_model.payloadId,
                            "timestamp": request_model.timestamp,
                            "telemetry": telemetry_data_dict,  # Will be None if not found in cache or DB
                        }
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )
                        log.info(
                            f"Sent telemetryResponse to {websocket.client.host} for payload {request_model.payloadId}."
                        )

                    except Exception as e:
                        log.error(
                            f"Error processing getTelemetry for {websocket.client.host}: {e}",
                            exc_info=True,
                        )
                        response.type = "error"
                        response.error = f"Failed to get telemetry: {e}"
                        await manager.send_personal_message(
                            response.model_dump_json(), websocket
                        )

                else:
                    # Handle unknown message type
                    log.warning(
                        f"Received unknown WebSocket message type: {msg_type} from {websocket.client.host}"
                    )
                    response.type = "error"
                    response.error = f"Unknown message type: {msg_type}"
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
                log.error(
                    f"Error processing WebSocket message loop for {websocket.client.host}: {e}",
                    exc_info=True,
                )
                # Attempt to send error back before disconnecting
                try:
                    await manager.send_personal_message(
                        json.dumps(
                            {"type": "error", "error": f"Internal server error: {e}"}
                        ),
                        websocket,
                    )
                except:
                    pass  # Ignore if sending fails
                break  # Exit loop on processing error

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {websocket.client.host}")
        # manager.disconnect handles cleanup
    except Exception as e:
        # Catch unexpected errors during the connection's main loop/setup
        log.error(
            f"Unexpected error for websocket {websocket.client.host}: {e}",
            exc_info=True,
        )
    finally:
        # Ensure disconnect cleanup happens
        manager.disconnect(websocket)


# --- HTTP Telemetry Endpoint (Alternative - ADAPTED FOR SCHEMA) ---
@router.get(
    "/telemetry",
    response_model=Optional[models.TelemetryData],  # Response can be null if not found
    summary="Get specific telemetry data point via HTTP",
    responses={
        200: {"description": "Telemetry data found or null if not found."},
        # 404 removed as we return null on 200 if not found
        500: {"description": "Internal Server Error"},
        503: {"description": "Database Service Unavailable"},
    },
)
async def get_telemetry_http(
    payloadId: int,  # Use int type hint
    timestamp: str,  # Keep as string, let DB handle casting/validation
    redis_client: redis.Redis = Depends(redis_client.get_redis),
):
    """
    Retrieves detailed telemetry for a specific payload ID and data_time timestamp.
    Checks cache first, then queries the database. Returns null if not found.
    """
    # TODO: Add validation for timestamp format if desired before DB query
    cache_key = f"telemetry:{payloadId}:{timestamp}"
    try:
        # 1. Check Cache
        cached_data = await redis_client.get(cache_key)
        if cached_data:
            log.debug(f"HTTP Telemetry cache hit for {cache_key}")
            # Parse and return directly using the model for validation
            return models.TelemetryData(**json.loads(cached_data))

        # 2. Cache Miss - Query Database
        log.debug(f"HTTP Telemetry cache miss for {cache_key}, querying DB.")
        async with database.get_db_connection() as db:
            # Use the adapted fetch_telemetry_data function
            telemetry_model = await fetch_telemetry_data(db, payloadId, timestamp)

        # 3. Cache Result (if found)
        if telemetry_model:
            telemetry_data_dict = telemetry_model.model_dump()
            await redis_client.set(
                cache_key, json.dumps(telemetry_data_dict), ex=3600
            )  # Cache 1hr

        # 4. Return Result (model or None) - FastAPI handles null correctly with Optional[]
        return telemetry_model

    except HTTPException as http_exc:
        # Re-raise HTTPExceptions raised by helpers (like DB unavailable)
        raise http_exc
    except Exception as e:
        log.error(f"HTTP Telemetry Error for payloadId={payloadId}: {e}", exc_info=True)
        # Raise generic 500 for other unexpected errors
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {e}")
