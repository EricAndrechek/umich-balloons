import logging
logger = logging.getLogger(__name__)

# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app

from ..helpers import db

from pydantic import ValidationError
from ..models.raw_messages import RawMessage
from ..models.packet import Packet, process_json_msg

import time
import json # If your raw data is JSON

# Define constants for clarity (match with watcher.py and celery.py queue defs)
IRIDIUM_RAW_LIST = 'iridium'  # The Redis list the watcher monitors
IRIDIUM_QUEUE = 'queue_iridium'        # The Celery queue this task uses

# Decorate the function as a Celery task, assigning it to the correct queue
@app.task(bind=True, queue=IRIDIUM_QUEUE)
def process_iridium(self, raw_data_item):
    """
    Processes a raw data item received from the Iridium raw list via the watcher.
    Args:
        raw_data_item (str): The raw data string popped from the Redis list.
    """
    logger.info(f"Task process_iridium received item (first 100 chars): {raw_data_item[:100]}")
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
    
    # Now we have a RawMessage object and can process the payload according to Iridium
    
    # no matter what, the 'payload' field should be uploaded to the database raw messages table
    # TODO: where do we use 'transmit_time'?

    # now we try to parse the payload as JSON
    parsed_payload = None
    try:
        parsed_payload = json.loads(raw_message.payload)
        logger.debug(f"Parsed payload: {parsed_payload}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON payload: {e}")
        # Handle the error or log it as needed
    except Exception as e:
        logger.error(f"Unexpected error while parsing payload: {e}")
        # Handle the error or log it as needed
    
    # get the payload ID from the database
    payload_id = get_payload_id(parsed_payload.callsign)
    
    # now we can compare the sender and timestamp we had from the raw message
    # with the parsed payload and work out which one was earliest and who we
    # should keep as the relay sender
    # TODO

    # now we can upload the parsed payload to the database
    # TODO

    # and associate it with the raw message
    # TODO

    # updating the raw message as needed
    # TODO