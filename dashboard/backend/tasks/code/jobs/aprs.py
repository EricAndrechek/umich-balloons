# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app
import logging
import time
import json # If your raw data is JSON

from aprspy import APRS

logger = logging.getLogger(__name__)

# Define constants for clarity (match with watcher.py and celery.py queue defs)
APRS_RAW_LIST = 'aprs'  # The Redis list the watcher monitors
APRS_QUEUE = 'queue_aprs'        # The Celery queue this task uses

# Decorate the function as a Celery task, assigning it to the correct queue
@app.task(bind=True, queue=APRS_QUEUE)
def process_aprs(self, raw_data_item):
    """
    Processes a raw data item received from the APRS raw list via the watcher.
    Args:
        raw_data_item (str): The raw data string popped from the Redis list.
    """
    logger.info(f"Task process_aprs received item: {raw_data_item[:100]}{'...' if len(raw_data_item) > 100 else ''}")
    try:
        # --- Your actual processing logic starts here ---
        try:
            data_dict = json.loads(raw_data_item)
            # should have "sender", "payload", and "timestamp" keys
            if not all(key in data_dict for key in ["sender", "payload", "timestamp"]):
                logger.error("Missing required keys in JSON data.")
                # ignore bad data and do not retry
                return "Invalid data format, task will not retry."
            
            # parse payload and add to db
            logger.info(f"Processing APRS packet from sender: {data_dict['sender']}")
            aprs = APRS.parse(data_dict['payload'])

            # print the parsed data
            logger.info(f"APRS packet parsed data: {aprs}")

        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from raw_data_item.")
            # ignore bad data and do not retry
            return "Invalid JSON data, task will not retry."

        logger.info(f"Task process_aprs finished successfully.")
        return "APRS data processed successfully."

    except Exception as e:
        logger.error(f"Error processing APRS data in task: {e}", exc_info=True)
        # Optional: Retry the task using Celery's mechanisms
        try:
            # Exponential backoff: 30s, 60s, 120s
            countdown = 30 * (2 ** self.request.retries)
            logger.warning(f"Retrying task {self.request.id} in {countdown} seconds...")
            self.retry(exc=e, countdown=countdown, max_retries=3)
        except self.MaxRetriesExceededError:
             logger.error(f"Max retries exceeded for task {self.request.id}.")
             # Potentially send to a dead-letter queue or log permanently
        except Exception as retry_error:
            logger.error(f"Error during retry for task {self.request.id}: {retry_error}", exc_info=True)

        # Optionally, you can raise the error again to mark the task as failed
        raise

# --- Add other APRS-related tasks below if needed ---
# @app.task(queue=APRS_QUEUE)
# def another_aprs_task(...):
#    ...