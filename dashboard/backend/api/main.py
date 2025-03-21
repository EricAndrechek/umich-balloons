from fastapi import FastAPI, Depends, Request
import os
import json
from supabase import create_client, Client
from dotenv import load_dotenv
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.DEBUG)

load_dotenv()

# Environment Variables
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "guest")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "guest")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "balloon-server")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://yourproject.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-anon-key")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgres://user:password@host:5432/dbname")

# Initialize FastAPI App
app = FastAPI()

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def upload_raw_message(message_data: dict):
    """Uploads a raw JSON message to the raw_messages table."""
    try:
        if "data_time" in message_data and isinstance(
            message_data["data_time"], datetime
        ):
            message_data["data_time"] = message_data["data_time"].isoformat()
        res = supabase.table("raw_messages").insert(message_data).execute()
        if res.data:
            return res.data[0]["id"]
        else:
            print(f"Error inserting raw message: {res.error}")
            return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
