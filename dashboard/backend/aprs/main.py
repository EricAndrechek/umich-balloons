import os
import json
from datetime import datetime, timezone
import base64
from sqlite3.dbapi2 import Timestamp
import threading
import functools

import redis.asyncio as redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError

import aprslib

import traceback
import asyncio

import logging
import colorlog

# --- Local ENV Configuration ---

# uncomment if you are running locally
# # Load environment variables from .env file
# from dotenv import load_dotenv
# load_dotenv()

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
if LOG_LEVEL not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
    LOG_LEVEL = 'INFO'  # Default to INFO if not set or invalid

# --- Logging Configuration ---

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("MAIN")
log.addHandler(handler)
log.setLevel(LOG_LEVEL)

logger = logging.getLogger(__name__)

# --- Configuration (from Environment Variables) ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_QUEUE_DB = int(os.getenv("REDIS_QUEUE_DB", 0))
REDIS_APRS_PUB_CHANNEL = os.getenv("REDIS_APRS_PUB_CHANNEL", "aprs")

APRS_IS_HOST = os.getenv("APRS_IS_HOST", "rotate.aprs.net")  # Use rotation service
APRS_IS_PORT = int(os.getenv("APRS_IS_PORT", 14580))  # Standard read-only port
APRS_IS_CALLSIGN = os.getenv("APRS_IS_CALLSIGN", "N0CALL")  # Replace with your callsign
APRS_IS_PASSWORD = os.getenv(
    "APRS_IS_PASSWORD", "-1"
)  # Generate one for your callsign!
APRS_FILTER = os.getenv(
    "APRS_FILTER", "s//#O"
)  # Replace with your desired filter, this is all balloons

# --- Redis Client Setup ---
redis_client = None

# Retry strategy for Redis connection
retry = Retry(ExponentialBackoff(), 5)

async def connect_redis():
    global redis_client
    try:
        log.info("Connecting to Redis...")
        redis_client = redis.from_url(
            REDIS_URL, db=REDIS_QUEUE_DB, decode_responses=True,
            retry=retry, retry_on_error=[ConnectionError, TimeoutError, BusyLoadingError]
        )
        await asyncio.wait_for(redis_client.ping(), timeout=5.0)
        log.info("Connected to Redis successfully.")
    except Exception as e:
        log.error(f"Failed to connect to Redis: {e}")
        # log.error(traceback.format_exc())
        redis_client = None

async def disconnect_redis():
    global redis_client
    if redis_client:
        await redis_client.aclose()
        log.info("Disconnected from Redis.")
        redis_client = None

# --- APRS Client Setup ---

async def callback(packet, reception_time):
    global redis_client
    if not redis_client:
        log.error("Redis client is not available. Cannot queue packet.")
        # TODO: Handle this case appropriately
        return

    try:
        packet = {
            "sender": "APRS-IS",
            "timestamp": reception_time,
            "payload": base64.b64encode(packet).decode("utf-8"),
            "ingest_method": "APRS-IS",
        }
        queue_number = await redis_client.rpush(REDIS_APRS_PUB_CHANNEL, json.dumps(packet))
        log.info(f"Packet queued with number: {queue_number}")

    except Exception as e:
        log.error(f"Error getting redis client: {e}")
        log.error(traceback.format_exc())


def sync_callback_wrapper(packet, loop):
    reception_time = datetime.now(timezone.utc).isoformat()
    log.info(f"Received packet: {packet}")
    loop.call_soon_threadsafe(asyncio.create_task, callback(packet, reception_time))

# packet1 = "KF8ABL-11>APRS,WIDE2-1:!4217.68N/08342.65WO182/000/A=000873"
# packet2 = "BD3QHE-5>APDR16,TCPIP*,qAC,T2SWEDEN:=3959.20N\11842.42EO/A=000209 https://aprsdroid.org/"
# packet3 = "A65MR>APOSB4,TCPIP*,qAC,T2DENMARK:@062327z2426.70N\\05434.49EO/A=000154SharkRF openSPOT4"
# packet4 = "KE2CFK-D>APDG03,TCPIP*,qAC,KE2CFK-DS:!4050.89N\\07359.42WO/A=00000070cm MMDVM Voice (DMR) 440.50000MHz -6.0000MHz, APRS for DMRGateway"


async def aprs_loop():
    log.info("Starting APRS loop...")
    loop = asyncio.get_running_loop()

    while True:
        AIS = None
        try:
            AIS = aprslib.IS(APRS_IS_CALLSIGN, passwd=APRS_IS_PASSWORD, host=APRS_IS_HOST, port=APRS_IS_PORT)
            AIS.set_filter(APRS_FILTER)

            # run blocking method in executor
            await loop.run_in_executor(None, AIS.connect)
            log.info("APRS-IS Connected successfully.")

            # Prepare the callback with the loop argument needed for call_soon_threadsafe
            # functools.partial freezes the 'loop' argument for the callback
            wrapped_callback = functools.partial(sync_callback_wrapper, loop=loop)

            # Run the blocking consumer() method in the executor
            log.info("Starting APRS-IS consumer...")
            consumer_with_args = functools.partial(
                AIS.consumer, wrapped_callback, raw=True
            )
            await loop.run_in_executor(
                None, consumer_with_args
            )

            # If consumer exits cleanly (e.g., server disconnect), log it and retry
            log.warning("APRS-IS consumer finished or disconnected. Reconnecting...")
            if AIS:
                try:
                    AIS.close()  # Attempt graceful close if possible
                except Exception as close_err:
                    log.warning(f"Error closing APRS connection: {close_err}")

        except aprslib.ConnectionError as e:
            log.error(f"APRS Connection Error: {e}. Retrying...")
        except aprslib.LoginError as e:
            log.error(f"APRS Login Error - aka we are on the one dumb java server")
        except Exception as e:
            log.error(f"Unexpected error in APRS loop: {e}")
            log.error(
                traceback.format_exc()
            )  # Log full traceback for unexpected errors
        finally:
            if AIS:  # Ensure cleanup even if consumer wasn't reached
                try:
                    # Check if AIS has a disconnect or close method
                    if hasattr(AIS, "close"):
                        AIS.close()
                    elif hasattr(AIS, "disconnect"):
                        AIS.disconnect()
                except Exception as close_err:
                    log.warning(f"Error during APRS cleanup: {close_err}")

        # Wait before retrying connection
        await asyncio.sleep(1)

async def main():
    log.info("Starting main function...")
    try:
        await connect_redis()
        await aprs_loop()
    except Exception as e:
        log.error(f"Critical error in main execution: {e}")
        log.error(traceback.format_exc())
    finally:
        log.info("Shutting down...")
        await disconnect_redis()
        log.info("Main function cleanup complete.")

if __name__ == "__main__":
    shutdown_event = asyncio.Event()  # Use an event for graceful shutdown

    loop = asyncio.get_event_loop()

    main_task = asyncio.ensure_future(main())

    try:
        loop.run_forever()  # Run until loop.stop() is called
    except KeyboardInterrupt:
        log.info("Shutdown initiated by user (KeyboardInterrupt).")
    except Exception as e:
        log.error(f"Unexpected error in event loop: {e}")
        log.error(traceback.format_exc())
    finally:
        log.info("Cleaning up asyncio tasks...")
        # Signal shutdown to tasks if necessary (using shutdown_event)
        # shutdown_event.set()

        # Give tasks a moment to finish cleanly
        # Gather pending tasks and cancel them
        tasks = asyncio.all_tasks(loop=loop)
        for task in tasks:
            if (
                task is not main_task and not task.done()
            ):  # Don't cancel main task directly here if it handles cleanup
                task.cancel()

        # Allow cancelled tasks to be processed
        # loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True)) # Causes issues if main() is still running cleanup

        # Ensure main task finishes its cleanup
        if not main_task.done():
            log.info("Waiting for main task cleanup...")
            loop.run_until_complete(main_task)  # Allow main's finally block to run

        loop.close()
        log.info("Event loop closed. Exiting program.")

        # Exit explicitly after cleanup
        exit(0)  # Use exit(0) for clean shutdown, exit(1) on error elsewhere
