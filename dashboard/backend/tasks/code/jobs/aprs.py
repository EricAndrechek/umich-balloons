import logging
logger = logging.getLogger(__name__)

# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app

from ..helpers import db

from pydantic import ValidationError
from ..models.raw_messages import RawMessage
from ..models.packet import ParsedPacket, process_json_msg

import time
import json
from datetime import datetime, timezone
from typing import Optional, Union
import uuid

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
    logger.info(f"Task process_aprs received item (first 100 chars): {raw_data_item[:100]}")
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
    
    # Now we have a RawMessage object and can process the payload according to APRS
    