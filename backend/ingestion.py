from fastapi import FastAPI, HTTPException
import paho.mqtt.client as mqtt
import os
import json
from pydantic import BaseModel, field_validator, ValidationError
from database import store_telemetry, check_duplicate
import time
from typing import Optional
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

ingestion_app = FastAPI()

MQTT_BROKER = os.environ.get("MQTT_BROKER")
MQTT_PORT = int(os.environ.get("MQTT_PORT"))

class TelemetryData(BaseModel):
    id: str
    lat: float
    lon: float
    alt: float
    speed: float
    source: str
    timestamp: Optional[int] = None

    @field_validator('lat', 'lon')
    def validate_coordinates(cls, value):
        if not -90 <= value <= 90:
            raise ValueError("Invalid latitude or longitude value")
        return value

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker!")
        client.subscribe("balloons/telemetry")
    else:
        print("Failed to connect, return code %d\n", rc)

def on_message(client, userdata, msg):
    print(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")
    try:
        payload = json.loads(msg.payload.decode())
        if 'timestamp' not in payload or payload['timestamp'] is None:
            payload['timestamp'] = int(time.time())
        telemetry_data = TelemetryData(**payload)

        # Deduplication check
        if not check_duplicate(telemetry_data.id, telemetry_data.timestamp):
            stored_data = store_telemetry(telemetry_data.model_dump())
            if stored_data:
                print("Telemetry data stored successfully:", stored_data)
            else:
                print("Failed to store telemetry data.")
        else:
            print(f"Duplicate telemetry data received for id: {telemetry_data.id} at timestamp: {telemetry_data.timestamp}")

    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
    except ValidationError as e:
        print(f"Error validating telemetry data: {e}")
    except Exception as e:
        print(f"Error processing telemetry data: {e}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_BROKER, MQTT_PORT)
client.loop_start()

@ingestion_app.get("/")
async def read_root():
    return {"message": "Ingestion API is running. Listening for MQTT messages."}
