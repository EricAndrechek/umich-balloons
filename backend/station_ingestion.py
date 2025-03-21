from fastapi import FastAPI, HTTPException
import paho.mqtt.client as mqtt
import os
import json
from pydantic import BaseModel, field_validator, ValidationError
from database import supabase  # Assuming supabase client is in database.py
import time
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

station_ingestion_app = FastAPI()

MQTT_BROKER = os.environ.get("MQTT_BROKER")
MQTT_PORT = int(os.environ.get("MQTT_PORT"))
STATION_TOPIC = "stations/data"

class StationData(BaseModel):
    station_id: str
    latitude: float
    longitude: float
    status: str
    timestamp: int

    @field_validator('latitude', 'longitude')
    def validate_coordinates(cls, value):
        if not -90 <= value <= 90:
            raise ValueError("Invalid latitude or longitude value")
        return value

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Station Ingestion Service connected to MQTT Broker!")
        client.subscribe(STATION_TOPIC)
    else:
        print("Failed to connect to MQTT, return code %d\n", rc)

def on_message(client, userdata, msg):
    print(f"Received station data on {msg.topic}: {msg.payload.decode()}")
    try:
        payload = json.loads(msg.payload.decode())
        station_data = StationData(**payload)
        # Store station data in Supabase (you might want a separate table for stations)
        response = supabase.table("stations").insert(station_data.model_dump()).execute()
        if response.data:
            print("Station data stored successfully:", response.data[0])
        else:
            print("Failed to store station data.")
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Error processing station data: {e}")
    except Exception as e:
        print(f"Unexpected error storing station data: {e}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT)
client.loop_start()

@station_ingestion_app.get("/")
async def read_root():
    return {"message": "Station Ingestion Service is running"}
