# code/helpers/db.py
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool # A standard pool implementation

from typing import Optional, Union

from ..models.callsign import Callsign
from ..models.packet import ParsedPacket

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

def execute_update(sql_query: str, params: dict = None):
    """
    Helper to execute INSERT/UPDATE/DELETE queries.
    Uses a transaction and returns the number of affected rows.
    """
    engine = get_engine()
    # Use a transaction block for DML statements
    try:
        with engine.connect() as connection:
            with connection.begin(): # Start a transaction (commits on success, rolls back on error)
                logger.debug(f"Executing update: {sql_query[:150]}... PARAMS: {params}")
                result = connection.execute(text(sql_query), params or {})
                rowcount = result.rowcount
                logger.debug(f"Update affected {rowcount} rows.")
            # Transaction committed successfully here
            return rowcount
    except SQLAlchemyError as e:
        logger.error(f"Database update/transaction error: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during update execution: {e}", exc_info=True)
        raise

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
    params = {'callsign': callsign.callsign}
    result = execute_query(sql_query, params)
    
    if result:
        # Payload found, return its ID
        return result[0][0]

    # Payload not found, create a new one
    sql_insert = """
    INSERT INTO payloads (callsign) VALUES (:callsign) RETURNING id
    """
    params = {'callsign': callsign.callsign}
    result = execute_update(sql_insert, params)
    
    if result:
        # Return the new payload ID
        return result[0][0]
    else:
        # Handle the case where the insert failed
        logger.error(f"Failed to create new payload for callsign: {callsign.callsign}")
        raise Exception("Failed to create new payload.")
    # Note: The above assumes the payloads table has a unique constraint on callsign
    # and that the ID is auto-incremented. Adjust as necessary for your schema.

def upload_raw_message(raw_message) -> int:
    """
    Upload a raw message to the database.
    Args:
        raw_message: The raw message to upload.
    Returns:
        message_id (int): The ID of the uploaded message in the database.
    """
    # turn the raw message into a JSON string
    if isinstance(raw_message, dict):
        raw_message = json.dumps(raw_message)
    elif isinstance(raw_message, bytes):
        raw_message = raw_message.decode('utf-8')
    else:
        try:
            logger.warning(f"Raw message is not str, dict, or bytes: {type(raw_message)}. Attempting to convert.")
            raw_message = str(raw_message)
        except Exception as e:
            logger.error(f"Failed to convert raw message to string: {e}")
            raise ValueError("Invalid raw message format. Must be dict, str, or bytes.")
    
    # Insert the raw message into the database
    # and return the ID of the new message
    sql_query = """
    INSERT INTO raw_messages (payload) VALUES (:payload) RETURNING id
    """