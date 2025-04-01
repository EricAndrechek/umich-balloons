# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app
import logging
import time
import json # If your raw data is JSON

from pydantic import ValidationError
from ..models.raw_messages import RawMessage

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
        # attempt to parse the raw data as RawMessage
        raw_message = RawMessage.parse_raw(raw_data_item)
        logger.debug(f"Parsed RawMessage: {raw_message}")

    except ValidationError as e:
        # ideally this should not be possible...
        # if the data is bad, it should still go to the raw messages table
        # and validation errors shouldn't happen here
        logger.error(f"Validation error: {e}")
        raise e
    
    # Now we have a RawMessage object and can process the payload according to LoRa
    