import asyncio
import logging
import os
import json

import redis.asyncio as redis
import aprslib

logging.basicConfig(level=logging.DEBUG) # level=10
logger = logging.getLogger(__name__)

# # --- Configuration (from Environment Variables) ---
# REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# REDIS_CALLSIGN_SUB_CHANNEL = os.getenv(
#     "REDIS_CALLSIGN_SUB_CHANNEL", "aprs_callsigns_to_monitor"
# )
# REDIS_APRS_PUB_CHANNEL = os.getenv("REDIS_APRS_PUB_CHANNEL", "aprs_data_feed")

APRS_IS_HOST = os.getenv("APRS_IS_HOST", "rotate.aprs.net")  # Use rotation service
APRS_IS_PORT = int(os.getenv("APRS_IS_PORT", 14580))  # Standard read-only port
APRS_IS_USER = os.getenv("APRS_IS_USER", "N0CALL")  # Replace with your callsign
APRS_IS_PASSCODE = os.getenv(
    "APRS_IS_PASSCODE", "-1"
)  # Generate one for your callsign!

AIS = aprslib.IS("N0CALL")
AIS.connect()
AIS.consumer(lambda x: None, raw=True)



