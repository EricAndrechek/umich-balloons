import aprs
import os
import json
from pydantic import ValidationError
from ingestion import TelemetryData  # Assuming TelemetryData is in ingestion.py
from database import store_telemetry, check_duplicate
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

APRS_USERNAME = os.environ.get("APRS_USERNAME")
APRS_PASSWORD = os.environ.get("APRS_PASSWORD")
APRS_FILTER = os.environ.get("APRS_FILTER")

class APRSClient:
    def __init__(self):
        self.client = None

    def connect(self):
        if not APRS_USERNAME or not APRS_PASSWORD:
            raise ValueError("APRS_USERNAME and APRS_PASSWORD must be set in the environment.")

        self.client = aprs.TCP(APRS_USERNAME, passwd=APRS_PASSWORD, filter=APRS_FILTER)
        self.client.connect()
        print("Connected to APRS-IS")

    def receive(self):
        if not self.client:
            raise Exception("Not connected to APRS-IS. Call connect() first.")

        for frame in self.client.iter():
            try:
                # APRS frame parsing
                if 'latitude' in frame and 'longitude' in frame:
                    lat = frame['latitude']
                    lon = frame['longitude']
                    alt = frame.get('altitude', 0)
                    speed = frame.get('speed', 0)
                    timestamp = frame.get('timestamp')  # Use frame timestamp if available
                    if timestamp:
                        timestamp = aprs.util.from_timestamp(timestamp)
                    else:
                        timestamp = None  # Handle cases with no timestamp

                    # Use callsign as the ID
                    callsign = frame.get('from_callsign', 'Unknown')

                    # Create a TelemetryData instance
                    telemetry_data = TelemetryData(
                        id=callsign,
                        lat=lat,
                        lon=lon,
                        alt=alt,
                        speed=speed,
                        source="APRS",
                        timestamp=int(timestamp.timestamp()) if timestamp else int(time.time())  # Use current time as fallback
                    )

                    # Deduplication and storage
                    if not check_duplicate(telemetry_data.id, telemetry_data.timestamp):
                        stored_data = store_telemetry(telemetry_data.model_dump())
                        if stored_data:
                            print("APRS telemetry data stored:", stored_data)
                        else:
                            print("Failed to store APRS telemetry data.")
                    else:
                        print(f"Duplicate APRS data: {telemetry_data.id} at {telemetry_data.timestamp}")
                else:
                    print("Incomplete APRS frame (missing lat/lon):", frame)

            except (KeyError, ValidationError) as e:
                print(f"Error processing APRS frame: {e}")
            except Exception as e:
                print(f"Unexpected error: {e}")

if __name__ == "__main__":
    try:
        client = APRSClient()
        client.connect()
        client.receive()
    except Exception as e:
        print(f"Error: {e}")
