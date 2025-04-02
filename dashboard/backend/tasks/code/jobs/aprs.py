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

from aprspy import APRS

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
    raw_msg_id = db.upload_raw_message(raw_message, transmit_method='APRS')

    parsed_aprs = {}

    # try to parse the APRS payload
    try:
        packet = APRS.parse(raw_message.payload)
        parsed_aprs['callsign'] = packet.source
        parsed_aprs['latitude'] = packet.latitude
        parsed_aprs['longitude'] = packet.longitude
        parsed_aprs['accuracy'] = packet.ambiguity
        if packet.altitude is not None:
            parsed_aprs['altitude'] = packet.altitude
        elif " ft" in packet.comment:
            # Extract altitude from comment if available
            try:
                altitude_feet = int(packet.comment.split(" ")[0])
                parsed_aprs['altitude'] = altitude_feet * 0.3048  # Convert feet to meters
            except ValueError:
                logger.warning(f"Could not parse altitude from comment: {packet.comment}")
        parsed_aprs['speed'] = packet.speed
        parsed_aprs['course'] = packet.course
        parsed_aprs['comment'] = packet.comment
        parsed_aprs['symbol_id'] = packet.symbol_id
        parsed_aprs['symbol_table'] = packet.symbol_table
        parsed_aprs['path'] = str(packet.path) if packet.path else None
        parsed_aprs['data_type_id'] = packet.data_type_id
        parsed_aprs['destination'] = packet.destination
        parsed_aprs['data_time'] = packet.timestamp if packet.timestamp else raw_message.timestamp

        # if packet = 'KF8ABL-11>APRS,WIDE2-1:!4217.67N/08342.78WO010/005100 ft'
        # >>> packet.data_type_id
        # '!'
        # >>> packet.destination
        # 'APRS'
        # >>> packet.info
        # '4217.67N/08342.78WO010/005100 ft'
        # >>> packet.path
        # <Path: WIDE2-1>
        # >>> packet.source
        # 'KF8ABL-11'
        # >>> packet.symbol_id
        # 'O'
        # >>> packet.symbol_table
        # '/'
        # >>> packet.latitude
        # 42.2945
        # >>> packet.longitude
        # -83.713
        # >>> packet.altitude
        # TODO: brian new packet with this working??
        # >>> packet.course
        # 10
        # >>> packet.speed
        # 5
        # >>> packet.comment
        # '100 ft'
        # >>> packet.ambiguity
        # 0
        # >>> packet.compressed
        # False
        # >>> packet.messaging
        # False
        # >>> packet.point
        # Point(42.2945, -83.713, None)
    
    except Exception as e:
        logger.error(f"Failed to parse APRS payload: {e}")
        raise e

    # now we try to parse the payload as JSON
    parsed_payload = None
    try:
        parsed_payload = process_json_msg(parsed_aprs)
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
        # TODO: trigger a task to let the broadcasters know about the new telemetry
        logger.info(f"New telemetry inserted with ID: {telemetry_id}")

    try:
        # TODO: work out if relay should be path or which path or left as-is

        sql_query = """
        UPDATE raw_messages
        SET source_id = :source_id,
            telemetry_id = :telemetry_id,
            sources = ARRAY[:source_id, :path] || sources,
            relay = :path
        WHERE id = :raw_msg_id;
        """
        params = {
            'source_id': parsed_payload.callsign,
            'telemetry_id': telemetry_id,
            'raw_msg_id': raw_msg_id,
            # TODO: this should be a list of sources, not just the first one
            'path': str(parsed_aprs['path']) if parsed_aprs['path'] else None
        }
        db.execute_update(sql_query, params)
        logger.info(f"Raw message {raw_msg_id} updated successfully.")
    except Exception as e:
        logger.error(f"Error updating raw message: {e}")
        raise e

    return telemetry_id