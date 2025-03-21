from fastapi import FastAPI, HTTPException
from database import get_latest_telemetry, subscribe_to_telemetry_updates
import paho.mqtt.client as mqtt
import os
import json
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

app = FastAPI()

MQTT_BROKER = os.environ.get("MQTT_BROKER")
MQTT_PORT = int(os.environ.get("MQTT_PORT"))
SYNC_TOPIC = "balloons/sync"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker for publishing!")
    else:
        print("Failed to connect to MQTT, return code %d\n", rc)

client = mqtt.Client()
client.on_connect = on_connect
client.connect(MQTT_BROKER, MQTT_PORT)

def publish_update(telemetry_data: dict):
    """Publishes telemetry updates to the MQTT broker."""
    try:
        # Include all telemetry data and confirmed sources (if available)
        payload = json.dumps(telemetry_data)
        client.publish(SYNC_TOPIC, payload)
        print(f"Published update to {SYNC_TOPIC}: {payload}")
    except Exception as e:
        print(f"Error publishing update: {e}")

subscribe_to_telemetry_updates(publish_update)  # Subscribe to Supabase updates

@app.get("/")
async def read_root():
    return {"message": "API Server is running"}

@app.get("/telemetry/{balloon_id}")
async def read_telemetry(balloon_id: str):
    telemetry_data = get_latest_telemetry(balloon_id)
    if telemetry_data:
        return telemetry_data
    else:
        raise HTTPException(status_code=404, detail="Telemetry data not found")

# Start the MQTT loop in a non-blocking way
client.loop_start()
