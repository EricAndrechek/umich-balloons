import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.pool import QueuePool  # A standard pool implementation
import psycopg2.errors

import uuid
from datetime import datetime, timezone
import json

import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("MAIN")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

POSTGRES_DB = os.getenv("POSTGRES_DB", "mydatabase")
POSTGRES_USERNAME = os.getenv("POSTGRES_USERNAME", "myuser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "mypassword")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))

DATABASE_URL = f"postgresql://{POSTGRES_USERNAME}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# create a database connection
conn = psycopg2.connect(DATABASE_URL)

# create a cursor
cur = conn.cursor()


def delete_payload(payload_id=None, callsign=None):
    """
    Delete a payload (and all its telemetry) from the database
    by either its database ID or its unique callsign.
    """

    # Check if either payload_id or callsign is provided
    if not payload_id and not callsign:
        log.error("Either payload_id or callsign must be provided.")
        return

    sql_query = """
    DELETE FROM payloads
    WHERE id = :payload_id OR callsign = :callsign
    RETURNING id;
    """
    params = {
        "payload_id": payload_id,
        "callsign": callsign
    }
    try:
        # Execute the SQL query
        cur.execute(sql_query, params)
        deleted_payload_id = cur.fetchone()

        # Commit the changes to the database
        conn.commit()
        log.info("Database changes committed.")

        if deleted_payload_id:
            log.info(f"Payload with ID {deleted_payload_id} deleted successfully.")
            return deleted_payload_id
        else:
            log.warning("No payload found with the provided ID or callsign.")
            return None

    except IntegrityError as e:
        log.error(f"Integrity error occurred: {e}")
        conn.rollback()
    except SQLAlchemyError as e:
        log.error(f"SQLAlchemy error occurred: {e}")
        conn.rollback()
    except Exception as e:
        log.error(f"An error occurred: {e}")
        conn.rollback()
    
def delete_all_payloads():
    """
    Delete all payloads from the database.
    """
    sql_query = "DELETE FROM payloads;"

    try:
        # Execute the SQL query
        cur.execute(sql_query)

        # Commit the changes to the database
        conn.commit()
        log.info("All payloads deleted successfully.")

    except IntegrityError as e:
        log.error(f"Integrity error occurred: {e}")
        conn.rollback()
    except SQLAlchemyError as e:
        log.error(f"SQLAlchemy error occurred: {e}")
        conn.rollback()
    except Exception as e:
        log.error(f"An error occurred: {e}")
        conn.rollback()
    
def delete_all_telemetry():
    """
    Delete all telemetry from the database.
    """
    sql_query = "DELETE FROM telemetry;"

    try:
        # Execute the SQL query
        cur.execute(sql_query)

        # Commit the changes to the database
        conn.commit()
        log.info("All telemetry deleted successfully.")

    except IntegrityError as e:
        log.error(f"Integrity error occurred: {e}")
        conn.rollback()
    except SQLAlchemyError as e:
        log.error(f"SQLAlchemy error occurred: {e}")
        conn.rollback()
    except Exception as e:
        log.error(f"An error occurred: {e}")
        conn.rollback()

def delete_raw_messages():
    """
    Delete all raw messages from the database.
    """
    sql_query = "DELETE FROM raw_messages;"

    try:
        # Execute the SQL query
        cur.execute(sql_query)

        # Commit the changes to the database
        conn.commit()
        log.info("All raw messages deleted successfully.")

    except IntegrityError as e:
        log.error(f"Integrity error occurred: {e}")
        conn.rollback()
    except SQLAlchemyError as e:
        log.error(f"SQLAlchemy error occurred: {e}")
        conn.rollback()
    except Exception as e:
        log.error(f"An error occurred: {e}")
        conn.rollback()

if __name__ == "__main__":
    delete_all_payloads()
    