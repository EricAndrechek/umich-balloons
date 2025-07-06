import logging
logger = logging.getLogger(__name__)

# Use relative import to get the app instance from celery.py in the parent directory
from ..celery import app

from ..helpers import db

from pydantic import ValidationError
from ..models.raw_messages import RawMessage
from ..models.packet import ParsedPacket, process_json_msg

import os
import time
import json
from datetime import datetime, timezone
from typing import Optional, Union
import uuid
import redis
import pygeohash as pgh

# --- Configuration ---
REDIS_QUEUE_DB = os.environ.get('REDIS_QUEUE_DB', '0') # Default Redis DB for queues
REDIS_CACHE_DB = os.environ.get('REDIS_CACHE_DB', '1') # Default Redis DB for cache
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/')


@app.task(bind=True, name='tasks.publish_raw_message')
def publish_raw_message(self, raw_message):
    """
    Dedicated task to publish raw messages to Redis.
    'result_data' comes from the preceding task in the chain.
    """
    if isinstance(raw_message, (dict, list)):
        payload_str = json.dumps(raw_message)
    elif not isinstance(raw_message, (str, bytes)):
        logger.error(f"Invalid payload type: {type(raw_message)}")
        raise ValueError("Payload must be a string, bytes, dict, or list.")
    else:
        payload_str = raw_message if isinstance(raw_message, str) else raw_message.decode('utf-8', errors='backslashreplace')

    logger.info(f"Publisher Task {self.request.id}: Received data to publish: {payload_str[:100]}")

    try:
        redis_client = redis.StrictRedis.from_url(REDIS_URL, db=REDIS_QUEUE_DB)
        redis_client.ping()
    
        channel_name = "raw-messages"
        timestamp = datetime.now(timezone.utc).isoformat()
        # Add timestamp to the payload
        payload_dict = {}
        payload_dict['ts'] = timestamp
        payload_dict['raw'] = payload_str
        payload_str = json.dumps(payload_dict)
        published_count = redis_client.publish(channel_name, payload_str)
        logger.info(f"Publisher Task {self.request.id}: Published to Redis channel '{channel_name}'. Subscribers: {published_count}")

        return {"status": "published", "channel": channel_name} # Or key/list name

    except redis.exceptions.ConnectionError as r_conn_err:
        print(f"ERROR: Publisher Task {self.request.id}: Could not connect to Redis - {r_conn_err}")
        raise self.retry(exc=r_conn_err, countdown=15)

    except Exception as pub_err:
        logger.error(f"ERROR: Publisher Task {self.request.id}: Failed to publish data to Redis - {pub_err}")
        raise self.retry(exc=pub_err, countdown=15)


@app.task(bind=True, name='tasks.publish_telemetry')
def publish_telemetry(self, result_data):
    """
    Dedicated task to publish received data to Redis.
    'result_data' comes from the preceding task in the chain.
    """
    if not isinstance(result_data, dict):
         logger.error(f"Publisher Task {self.request.id}: Received non-dict data: {type(result_data)}. Skipping publish.")
         # Or handle appropriately - maybe try to serialize anyway?
         return {"status": "skipped", "reason": "invalid data type"}

    logger.info(f"Publisher Task {self.request.id}: Received data to publish: {result_data}")
    task_id = result_data.get('task_id', 'unknown') # Get original task ID if passed

    try:
        redis_client = redis.StrictRedis.from_url(REDIS_URL, db=REDIS_QUEUE_DB)
        redis_client.ping()
        
        # get geohash from lat/lon
        geohash_str = pgh.encode(latitude=result_data['latitude'], longitude=result_data['longitude'], precision=8)
        # Add geohash_str to the result_data
        result_data['geohash_str'] = geohash_str

        channel_name = "realtime-updates"
        json_output = json.dumps(result_data)
        published_count = redis_client.publish(channel_name, json_output)
        logger.info(f"Publisher Task {self.request.id}: Published to Redis channel '{channel_name}'. Subscribers: {published_count}")

        return {"status": "published", "channel": channel_name} # Or key/list name

    except redis.exceptions.ConnectionError as r_conn_err:
        print(f"ERROR: Publisher Task {self.request.id}: Could not connect to Redis - {r_conn_err}")
        raise self.retry(exc=r_conn_err, countdown=15)

    except Exception as pub_err:
        logger.error(f"ERROR: Publisher Task {self.request.id}: Failed to publish data to Redis - {pub_err}")
        raise self.retry(exc=pub_err, countdown=15)