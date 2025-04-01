import redis
import os
import logging
import time
import json # If you need to validate JSON before sending

# Use relative import to get the app instance from celery.py in the same directory
from .celery import app as celery_app

# Configure basic logging for the watcher
LOG_LEVEL = os.environ.get('ENV_LOG_LEVEL', 'INFO').upper()
if LOG_LEVEL not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
    LOG_LEVEL = 'INFO'  # Default to INFO if not set or invalid
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - watcher - %(message)s')

# Set up logging for the watcher
logger = logging.getLogger(__name__)

# --- Configuration ---
REDIS_QUEUE_DB = os.environ.get('REDIS_QUEUE_DB', '0') # Default Redis DB for queues
REDIS_CACHE_DB = os.environ.get('REDIS_CACHE_DB', '1') # Default Redis DB for cache
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/')

# Map Raw Redis List Names to Celery Task Paths and Queue Names
# IMPORTANT: Ensure task paths ('code.jobs.module.function') and queue names match your setup
WATCH_MAP = {
    # Raw Redis List Name : ('full.celery.task.path.string', 'celery_queue_name')
    'aprs': ('code.jobs.aprs.process_aprs', 'queue_aprs'),
    'iridium': ('code.jobs.iridium.process_iridium', 'queue_iridium'),
    'lora': ('code.jobs.lora.process_lora', 'queue_lora'),

    # Manual triggers
     # --- Manual Trigger for Flight Prediction ---
    # Assumes manual triggers push data to 'raw_list_flight_prediction'
    # Points to a task designed to handle manually triggered data
    'predict_flight': ('code.jobs.flight_prediction.handle_manual_prediction_request', 'queue_predictions'),
    # --- Manual Trigger for Path Generator ---
    # Assumes manual triggers push data to 'raw_list_path_gen'
    # Points to a task designed to handle manually triggered data
    'get_path': ('code.jobs.path_generator.handle_manual_path_request', 'queue_path_gen'),
}

# Extract list names for the BLPOP command
lists_to_watch = list(WATCH_MAP.keys())
if not lists_to_watch:
    logging.error("Watcher configuration error: No lists defined in WATCH_MAP.")
    exit(1) # Exit if configuration is empty


# --- Watcher Main Loop ---
def run_watcher():
    logging.info(f"Watcher starting. Connecting to Redis at {REDIS_URL}")
    logging.info(f"Watching raw lists: {lists_to_watch}")
    redis_client = None # Initialize client variable

    while True:
        try:
            # Ensure connection exists or reconnect
            if redis_client is None:
                 logging.info("Establishing Redis connection...")
                 redis_client = redis.Redis.from_url(REDIS_URL, db=REDIS_QUEUE_DB, decode_responses=True) # Get strings from Redis
                 redis_client.ping() # Verify connection
                 logging.info("Redis connection successful.")

            # Block and wait for data on any of the specified lists
            # timeout=0 blocks indefinitely
            logging.debug(f"Waiting for data on {lists_to_watch}...")
            source_list, raw_data = redis_client.blpop(lists_to_watch, timeout=0)
            logging.info(f"Received item from '{source_list}'. Size: {len(raw_data)} bytes.")
            logging.debug(f"Raw data snippet: {raw_data[:100]}") # Log only a snippet

            # Optional: Basic validation (e.g., check if it looks like JSON)
            # try:
            #     json.loads(raw_data) # Try parsing to see if valid JSON
            # except json.JSONDecodeError:
            #     logging.warning(f"Received non-JSON data from {source_list}, skipping task dispatch.")
            #     continue # Skip sending this item to Celery

            # Look up the corresponding task and queue
            if source_list in WATCH_MAP:
                task_name, queue_name = WATCH_MAP[source_list]
                logging.debug(f"Dispatching item to task='{task_name}', queue='{queue_name}'")

                # Send the raw data as the first argument to the Celery task
                celery_app.send_task(task_name, args=[raw_data], queue=queue_name)
                logging.debug(f"Item successfully sent to Celery.")

            else:
                # This case should technically not be reachable with blpop if lists_to_watch is correct
                logging.warning(f"Received data from an unexpected list '{source_list}' - check WATCH_MAP.")

        except redis.exceptions.ConnectionError as e:
            logging.error(f"Redis connection error: {e}. Resetting connection and retrying in 10 seconds...")
            redis_client = None # Force reconnection attempt in the next loop
            time.sleep(10)
        except redis.exceptions.TimeoutError:
            # This happens if blpop has a timeout and nothing is received.
            # With timeout=0, this shouldn't occur, but good practice to handle.
            logging.debug("BLPOP timed out (if timeout > 0). Continuing.")
            continue
        except Exception as e:
            # Catch-all for other unexpected errors (e.g., task sending issues)
            logging.error(f"An unexpected error occurred in the watcher loop: {e}", exc_info=True)
            # Implement a backoff strategy to prevent fast looping on persistent errors
            time.sleep(5)

if __name__ == "__main__":
    run_watcher()