# code/helpers/db.py
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.pool import QueuePool # A standard pool implementation
import psycopg2.errors

import uuid
from datetime import datetime, timezone
import json

from typing import Optional, Union

from ..models.callsign import Callsign
from ..models.packet import ParsedPacket
from ..models.raw_messages import RawMessage

logger = logging.getLogger(__name__)

# This global variable will hold the engine instance *per process*.
_engine = None

def get_engine():
    """
    Creates and returns a SQLAlchemy engine with a connection pool.
    Uses a process-local global variable (_engine) so that each
    Celery worker process initializes its own engine/pool once.
    """
    global _engine
    if _engine is None:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            logger.critical("DATABASE_URL environment variable is not set!")
            raise ValueError("DATABASE_URL is required for database operations.")

        # --- Pool Configuration (Read from Environment Variables) ---
        # Calculate total connections: workers * concurrency * (pool_size + max_overflow)
        # Ensure this total doesn't exceed postgres max_connections.
        pool_size = int(os.environ.get('DB_POOL_SIZE', '5'))
        max_overflow = int(os.environ.get('DB_MAX_OVERFLOW', '2')) # Allows temporary bursts
        pool_recycle_seconds = int(os.environ.get('DB_POOL_RECYCLE', '3600')) # Recycle connections hourly

        # Log initialization specific to this process ID
        pid = os.getpid()
        logger.info(
            f"[DB Init PID:{pid}] Initializing DB engine. "
            f"URL: {db_url.split('@')[-1]}, " # Log DB host/db without creds
            f"Pool Size: {pool_size}, Max Overflow: {max_overflow}, "
            f"Recycle (s): {pool_recycle_seconds}"
        )

        try:
            _engine = create_engine(
                db_url,
                poolclass=QueuePool,      # Explicitly select pool type (optional, usually default)
                pool_size=pool_size,      # Max persistent connections per pool/process
                max_overflow=max_overflow,# Max temporary overflow connections per pool/process
                pool_recycle=pool_recycle_seconds, # Close connections older than this (seconds)
                pool_pre_ping=True,       # Check connection validity before use
                pool_timeout=10,          # Seconds to wait for a connection from pool before timing out
                # Example connect_args if needed for psycopg2
                # connect_args={"connect_timeout": 5, "application_name": f"celery_worker_{pid}"}
            )
            logger.info(f"[DB Init PID:{pid}] DB engine initialization complete.")
        except Exception as e:
             logger.critical(f"[DB Init PID:{pid}] Failed to create database engine: {e}", exc_info=True)
             # Optionally re-raise or handle appropriately
             raise

    return _engine

def execute_query(sql_query: str, params: dict = None):
    """
    Helper to execute SELECT queries. Fetches all results.
    Uses a connection from the pool managed by the engine.
    """
    engine = get_engine() # Get the engine for this process
    try:
        with engine.connect() as connection: # Borrow connection from pool
            logger.debug(f"Executing query: {sql_query[:150]}... PARAMS: {params}")
            result = connection.execute(text(sql_query), params or {})
            rows = result.fetchall() # Returns list of Row objects
            logger.debug(f"Query fetched {len(rows)} rows.")
            return rows
    except SQLAlchemyError as e:
        logger.error(f"Database query error: {e}", exc_info=True)
        # Decide how to handle DB errors - re-raise, return None, etc.
        raise # Re-raise by default
    except Exception as e:
        logger.error(f"Unexpected error during query execution: {e}", exc_info=True)
        raise

def execute_update(sql_query: str, params: dict = None, return_full_row: bool = False) -> Union[int, uuid.UUID, None]:
    """
    Helper to execute INSERT/UPDATE/DELETE queries.
    Uses a transaction.
    Returns the first value from a RETURNING clause if present (e.g., the inserted ID),
    otherwise returns the number of affected rows (rowcount).
    Returns None if RETURNING was expected but nothing was returned.
    """
    engine = get_engine()
    returned_value = None
    try:
        with engine.connect() as connection:
            with connection.begin(): # Start a transaction
                logger.debug(f"Executing update: {sql_query[:150]}... PARAMS: {params}")
                result = connection.execute(text(sql_query), params or {})
                
                # Try to fetch the result from RETURNING if the cursor supports it
                if result.returns_rows:
                    if return_full_row:
                        # Fetch and return the full Row object (or None)
                        returned_value = result.fetchone()
                        if returned_value:
                            logger.debug(f"Update returned full row: {returned_value}")
                        else:
                            logger.debug("Update with return_full_row=True returned no row.")
                    else:
                        # Fetch and return only the first scalar value (or None) - OLD BEHAVIOR
                        returned_value = result.scalar()
                        logger.debug(f"Update returned scalar value: {returned_value}")
                else:
                    # Fallback or logging if no RETURNING clause was detected/used
                    # For simplicity, we assume RETURNING is usually intended now
                    row_count = result.rowcount
                    logger.debug(f"Update affected {row_count} rows (no RETURNING clause detected or no rows returned?).")
                    # Return rowcount only if nothing else was returned? Or always None if no RETURNING?
                    # Let's stick to returning None if RETURNING didn't yield value based on flag.
                    returned_value = None # Explicitly None if not fetched via scalar/fetchone

            # Transaction committed successfully here
            return returned_value

    except SQLAlchemyError as e:
        logger.error(f"Database update/transaction error: {e}", exc_info=True)
        # Transaction automatically rolled back by 'connection.begin()' context manager on error
        raise
    except Exception as e:
        logger.error(f"Unexpected error during update execution: {e}", exc_info=True)
        raise

# -------------------------------
# --- Payload Table Functions ---
# -------------------------------

def get_payload_id(callsign: Callsign) -> int:
    """
    Given a callsign, find if a payload mapped to one of them exists in the database.
    If a payload is not found, create a new one.
    Args:
        callsign (str): The callsign of the payload.
    Returns:
        payload_id (int): The ID of the payload in the database.
    """

    # Check if the payload already exists
    sql_query = """
    SELECT id FROM payloads WHERE callsign = :callsign
    """
    params = {'callsign': callsign}
    result = execute_query(sql_query, params)
    
    if result:
        # Payload found, return its ID
        return result[0][0]

    # Payload not found, create a new one
    sql_insert = """
    INSERT INTO payloads (callsign) VALUES (:callsign) RETURNING id
    """
    params = {'callsign': callsign}
    result = execute_update(sql_insert, params)
    
    if isinstance(result, int):
        # If the result is an integer, it means the insert was successful
        logger.info(f"Created new payload with ID: {result} for callsign: {callsign}")
        return result
    else:
        # Handle the case where the insert failed
        logger.error(f"Failed to create new payload for callsign: {callsign}")
        raise Exception("Failed to create new payload.")

# -----------------------------------
# --- Raw Message Table Functions ---
# -----------------------------------

def upload_raw_message(raw_message: RawMessage, transmit_method: str, ingest_method: Optional[str] = None, relay: Optional[str] = None) -> int:
    """
    Upload a raw message to the database.
    Args:
        raw_message: The RawMessage object to upload with sender as source_id and 
            first item in 'sources' array, payload as raw_data, and timestamp as data_time.
        ingest_method (str): The method used to ingest the message (e.g., "HTTP", "MQTT").
        transmit_method (str): The method used by the original sender (e.g., "APRS", "Iridium").
        relay (optional)(str): The device that relayed the message to us. 
            (Callsign, IMEI/Serial, etc.) (defaults to raw_message.sender)
    Returns:
        message_id (int): The ID of the uploaded message in the database.
    """
    # Make raw_message.payload into a JSON string
    if isinstance(raw_message.payload, (dict, list)):
        payload_str = json.dumps(raw_message.payload)
    elif not isinstance(raw_message.payload, str):
        logger.error(f"Invalid payload type: {type(raw_message.payload)}")
        raise ValueError("Payload must be a string, dict, or list.")
    else:
        payload_str = raw_message.payload
    
    # Prepare the SQL query
    sql_insert = """
    INSERT INTO raw_messages (source_id, sources, raw_data, ingest_method, transmit_method, relay)
    VALUES (:source_id, ARRAY[:source_id, 'UMICH-BALLOONS'], :raw_data, :ingest_method, :transmit_method, :relay)
    RETURNING id
    """
    params = {
        'source_id': raw_message.sender,
        'raw_data': payload_str,
        'ingest_method': ingest_method or raw_message.ingest_method,
        'transmit_method': transmit_method,
        'relay': relay or raw_message.sender
    }
    try:
        # Execute the insert query
        message_id = execute_update(sql_insert, params)

        if isinstance(message_id, int):
            # If the result is an integer, it means the insert was successful
            logger.info(f"Raw message uploaded successfully with ID: {message_id}")
            return message_id
        else:
            logger.error(f"Failed to upload raw message, unexpected result type: {type(message_id)}")
            raise ValueError("Failed to upload raw message, unexpected result type.")
    except SQLAlchemyError as e:
        logger.error(f"Failed to upload raw message: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during raw message upload: {e}", exc_info=True)
        raise

# ---------------------------------
# --- Telemetry Table Functions ---
# ---------------------------------

def upload_telemetry(telemetry: ParsedPacket, payload_id: int) -> tuple[uuid.UUID, bool]:
    """
    Upload telemetry data to the database.
    Args:
        telemetry (ParsedPacket): The telemetry data to upload. This should be a ParsedPacket object with the following db mappings:
            data_time: telemetry.data_time
            position: POINT(telemetry.latitude, telemetry.longitude)
            altitude: telemetry.altitude
            speed: telemetry.speed
            course: telemetry.course
            battery: telemetry.battery
            extra: telemetry.extra
        payload_id (int): The ID of the payload in the database: payload_id
    Returns:
        telemetry_id (uuid): The ID of the uploaded telemetry in the database.
        If this is a duplicate (based on constraint (payload_id, data_time)), returns the ID of the existing telemetry.
        If this is existing telemetry but this source had more accurate position data, update the position data.
        If this is existing telemetry but this source had any fields that were null and now have values, update those fields.
        If this is new telemetry, insert it and return the new ID.

        and a boolean indicating if it was inserted (true) or updated (false).
    """
    if not all([hasattr(telemetry, attr) for attr in ['latitude', 'longitude', 'data_time']]):
        raise ValueError("Telemetry packet missing essential fields (latitude, longitude, data_time).")
    if payload_id is None:
        raise ValueError("payload_id cannot be None.")

    params = {
        'payload_id': payload_id,
        'data_time': telemetry.data_time.isoformat() if telemetry.data_time else None,

        'latitude': telemetry.latitude,
        'longitude': telemetry.longitude,
        'accuracy': telemetry.accuracy,
        'altitude': telemetry.altitude,
        'speed': telemetry.speed,
        'course': telemetry.course,
        'battery': telemetry.battery,
        'extra': json.dumps(telemetry.model_dump(mode='json')['extra']) if telemetry.extra is not None else None
    }

    sql_query = """
    INSERT INTO public.telemetry (
        payload_id, data_time, position, accuracy, altitude,
        speed, course, battery, extra
    ) VALUES (
        :payload_id, :data_time, ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326),
        :accuracy, :altitude, :speed, :course, :battery, :extra
    )
    ON CONFLICT (payload_id, data_time)
    DO UPDATE SET
        position = CASE
                     WHEN EXCLUDED.accuracy IS NOT NULL AND (telemetry.accuracy IS NOT NULL AND EXCLUDED.accuracy < telemetry.accuracy)
                     THEN EXCLUDED.position
                     ELSE telemetry.position
                   END,
        accuracy = CASE
                     WHEN EXCLUDED.accuracy IS NOT NULL AND (telemetry.accuracy IS NOT NULL AND EXCLUDED.accuracy < telemetry.accuracy)
                     THEN EXCLUDED.accuracy
                     ELSE telemetry.accuracy
                   END,
        altitude = CASE
                        WHEN EXCLUDED.altitude IS NOT NULL AND (telemetry.altitude IS NULL)
                        THEN EXCLUDED.altitude
                        ELSE telemetry.altitude
                    END,
        speed = CASE
                    WHEN EXCLUDED.speed IS NOT NULL AND (telemetry.speed IS NULL)
                    THEN EXCLUDED.speed
                    ELSE telemetry.speed
                END,
        course = CASE
                     WHEN EXCLUDED.course IS NOT NULL AND (telemetry.course IS NULL)
                     THEN EXCLUDED.course
                     ELSE telemetry.course
                 END,
        battery = CASE
                     WHEN EXCLUDED.battery IS NOT NULL AND (telemetry.battery IS NULL)
                     THEN EXCLUDED.battery
                     ELSE telemetry.battery
                 END,
        extra = CASE
                    WHEN EXCLUDED.extra IS NOT NULL AND (telemetry.extra IS NULL)
                    THEN EXCLUDED.extra
                    ELSE telemetry.extra
                END,
        last_updated = (now() AT TIME ZONE 'utc')
    RETURNING id, xmax;
    """

    # --- Execute UPSERT ---
    try:
        result_row = execute_update(sql_query, params, return_full_row=True)

        if result_row:
            telemetry_id = result_row[0]
            xmax_val = result_row[1]

            if isinstance(telemetry_id, uuid.UUID):
                # xmax = 0 indicates the row was newly inserted by this transaction
                # xmax != 0 indicates the row existed and was potentially updated (or just locked) by this transaction
                was_inserted = (xmax_val == 0)
                action = "inserted" if was_inserted else "updated/found"
                logger.info(f"Telemetry {action}. ID: {telemetry_id} for payload {payload_id} (xmax={xmax_val})")
                return telemetry_id, was_inserted # Return tuple (id, was_inserted_boolean)
            else:
                 # Should not happen if RETURNING id works
                logger.error(f"Upsert returned unexpected type for id: {type(telemetry_id)} for payload {payload_id}")
                raise Exception("Failed to retrieve valid telemetry ID after upsert.")
        else:
            # This could happen if ON CONFLICT DO UPDATE had a WHERE clause that failed,
            # and PostgreSQL doesn't return rows in that specific edge case.
            # Or if the underlying execute_update failed to fetch.
            logger.error(f"Telemetry upsert did not return a row for payload {payload_id}. Check DB logs/UPSERT logic if updates aren't happening as expected.")
            raise Exception("Telemetry upsert failed to return expected data.")

    except IntegrityError as e:
         # This might catch other integrity errors, but primarily the unique constraint
         # if the ON CONFLICT clause somehow failed or wasn't appropriate.
         logger.error(f"Database integrity error during telemetry upsert for payload {payload_id}: {e}", exc_info=True)
         # Depending on the exact error, you might try to recover or just re-raise
         # If it's the specific UniqueViolation and ON CONFLICT *should* have handled it, something is wrong.
         raise # Re-raise integrity errors for now
    except SQLAlchemyError as e:
        logger.error(f"Database error during telemetry upsert for payload {payload_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during telemetry upsert for payload {payload_id}: {e}", exc_info=True)
        raise