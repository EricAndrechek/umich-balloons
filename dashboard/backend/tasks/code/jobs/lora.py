# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app
import logging
import time
import json # If your raw data is JSON

logger = logging.getLogger(__name__)

# Define constants for clarity (match with watcher.py and celery.py queue defs)
LORA_RAW_LIST = 'lora'  # The Redis list the watcher monitors
LORA_QUEUE = 'queue_lora'        # The Celery queue this task uses

# Decorate the function as a Celery task, assigning it to the correct queue
@app.task(bind=True, queue=LORA_QUEUE)
def process_lora(self, raw_data_item):
    """
    Processes a raw data item received from the lora raw list via the watcher.
    Args:
        raw_data_item (str): The raw data string popped from the Redis list.
    """
    logger.info(f"Task process_lora received item (first 100 chars): {raw_data_item[:100]}")
    try:
        # --- Your actual processing logic starts here ---
        # Example: If the raw data is expected to be JSON
        # try:
        #     data_dict = json.loads(raw_data_item)
        #     logger.info(f"Processing LORA packet ID: {data_dict.get('id', 'N/A')}")
        #     # ... further processing using data_dict ...
        # except json.JSONDecodeError:
        #     logger.error("Failed to decode JSON from raw_data_item.")
        #     # Decide how to handle non-JSON data - raise error, ignore, etc.
        #     raise # Example: Fail the task if JSON is expected

        # Simulate work
        time.sleep(0.5) # Simulate processing time
        result_summary = f"Successfully processed lora data starting with: {raw_data_item[:50]}..."
        # --- Your actual processing logic ends here ---

        logger.info(f"Task process_lora finished successfully.")
        return result_summary # Optional: Return a result

    except Exception as e:
        logger.error(f"Error processing lora data in task: {e}", exc_info=True)
        # Optional: Retry the task using Celery's mechanisms
        # try:
        #     # Exponential backoff: 30s, 60s, 120s
        #     countdown = 30 * (2 ** self.request.retries)
        #     logger.warning(f"Retrying task {self.request.id} in {countdown} seconds...")
        #     self.retry(exc=e, countdown=countdown, max_retries=3)
        # except self.MaxRetriesExceededError:
        #      logger.error(f"Max retries exceeded for task {self.request.id}.")
        #      # Potentially send to a dead-letter queue or log permanently
        raise # Re-raise the exception to mark the task as FAILED