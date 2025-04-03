import logging
logger = logging.getLogger(__name__)

# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app

from ..helpers import db

from pydantic import ValidationError
from ..models.raw_messages import RawMessage
from ..models.packet import ParsedPacket, process_json_msg
from ..jobs.broadcast import publish_telemetry

import time
import json
from datetime import datetime, timezone
from typing import Optional, Union
import uuid

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
    # no matter what, the 'payload' field should be uploaded to the database raw messages table
    raw_msg_id = db.upload_raw_message(raw_message, transmit_method='LoRa')

    # try to parse the LoRa payload
    parsed_payload = None
    try:
        parsed_payload = process_json_msg(raw_message.payload)
        logger.debug(f"Parsed payload: {parsed_payload}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON payload: {e}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error while parsing payload: {e}")
        raise e
    
    # get the payload ID from the database
    payload_id = db.get_payload_id(parsed_payload.callsign)
    logger.info(f"Payload ID retrieved: {payload_id}")

    # check if the internal data_time field is before or after the raw_message timestamp
    # past < present == True
    # if (what should be the past) is after (what should be the present)
    try:
        if parsed_payload.data_time > raw_message.timestamp:
            # this is a problem, we need to fix it
            # set parsed_payload.data_time to the raw_message timestamp
            parsed_payload.data_time = raw_message.timestamp
            logger.warning(f"Adjusted parsed_payload.data_time to match raw_message.timestamp: {parsed_payload.data_time}")
    except TypeError as e:
        logger.error(f"Type error while comparing timestamps: {e}")
        # this likely means the timestamps are not comparable (eg one is timezone aware and the other is not)
        # TODO: handle this case
    
    # upload the parsed payload to the database (or confirm it exists/verify it)
    
    try:
        telemetry_id, was_inserted = db.upload_telemetry(telemetry=parsed_payload, payload_id=payload_id)
        logger.info(f"Telemetry uploaded successfully with ID: {telemetry_id}")
    except Exception as e:
        logger.error(f"Error uploading telemetry: {e}")
        raise e

    if was_inserted:
        update_packet = {
            'telemetry_id': str(telemetry_id),
            'payload_id': str(payload_id),
            'lon': parsed_payload.longitude,
            'lat': parsed_payload.latitude,
            'ts': parsed_payload.data_time.isoformat(),
        }
        publish_telemetry.delay(update_packet)
        logger.info(f"New telemetry inserted with ID: {telemetry_id}")

    try:
        # update raw message with telemetry ID and some parsed data fields, include:
        # - source_id = parsed_payload.callsign
        # - telemetry_id = telemetry_id
        # - sources = [parsed_payload.callsign] || sources
        sql_query = """
        UPDATE raw_messages
        SET source_id = :source_id,
            telemetry_id = :telemetry_id,
            sources = ARRAY[:source_id] || sources
        WHERE id = :raw_msg_id;
        """
        params = {
            'source_id': parsed_payload.callsign,
            'telemetry_id': telemetry_id,
            'raw_msg_id': raw_msg_id
        }
        db.execute_update(sql_query, params)
        logger.info(f"Raw message {raw_msg_id} updated successfully.")
    except Exception as e:
        logger.error(f"Error updating raw message: {e}")
        raise e

    return telemetry_id
    