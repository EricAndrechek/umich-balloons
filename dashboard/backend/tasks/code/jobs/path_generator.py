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

PATH_GEN_QUEUE = 'queue_path_gen'

# --- Task for Scheduled Runs ---
@app.task(bind=True, queue=PATH_GEN_QUEUE)
def run_scheduled_path_generation(self, *args, **kwargs):
    """
    Task executed by Celery Beat based on the schedule defined in celery.py.
    It might receive specific args/kwargs from the beat schedule definition.
    """
    logger.info(f"Running SCHEDULED path generation. Task ID: {self.request.id}")
    logger.debug(f"Scheduled run args: {args}, kwargs: {kwargs}")
    try:
        # connect to db and run
        # REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_payload_path_segments;

        db.refresh_materialized_view('mv_payload_path_segments')
        result = "Materialized view refreshed successfully."
        logger.info(result)
        
        return result
    except Exception as e:
        logger.error(f"Error during scheduled flight prediction: {e}", exc_info=True)
        # Add retry logic if applicable
        raise

# --- Task for Manual Triggers (via Watcher) ---
@app.task(bind=True, queue=PATH_GEN_QUEUE)
def handle_manual_path_request(self, raw_data_item):
    """
    Task executed when data is pushed to the 'raw_list_path_gen'
    and picked up by the watcher.
    """
    logger.info(f"Handling MANUAL path request. Task ID: {self.request.id}")
    logger.debug(f"Manual trigger raw data (first 100): {raw_data_item[:100]}")
    try:
        # --- Add logic for manually triggered prediction ---
        # Example: Parse raw_data_item (e.g., if JSON) to get specific flight ID or parameters
        # try:
        #     request_params = json.loads(raw_data_item)
        #     flight_id = request_params.get('flight_id')
        #     if not flight_id:
        #         raise ValueError("Missing 'flight_id' in manual request data")
        #     logger.info(f"Generating prediction for specific flight: {flight_id}")
        #     # ... perform prediction based on flight_id ...
        # except (json.JSONDecodeError, ValueError) as parse_error:
        #     logger.error(f"Invalid manual request data: {parse_error}")
        #     # Don't retry bad data - fail the task
        #     # You might want specific error handling here
        #     raise ValueError(f"Invalid request data: {parse_error}") from parse_error

        logger.info("Performing manual prediction calculation...")
        time.sleep(5) # Simulate work based on request
        result = f"Manual prediction completed for request: {raw_data_item[:50]}..."
        # --- End manual prediction logic ---

        logger.info("Manual path request finished successfully.")
        return result
    except Exception as e:
        logger.error(f"Error during manual path request: {e}", exc_info=True)
        # Decide on retry strategy based on the type of error
        raise

# You would apply a similar pattern (separate scheduled/manual tasks or a combined one
# with logic branching) to code/jobs/path_generator.py