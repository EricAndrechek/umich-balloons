from supabase import create_client, Client
import os
from typing import Optional, Callable
import json
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    raise EnvironmentError("SUPABASE_URL and SUPABASE_KEY must be set in the environment.")

supabase: Client = create_client(url, key)

def store_telemetry(data: dict) -> Optional[dict]:
    """Stores telemetry data in the database."""
    try:
        response = supabase.table("telemetry").insert(data).execute()
        if response.data:
            return response.data[0]  # Return the inserted record
        else:
            return None
    except Exception as e:
        print(f"Error storing telemetry data: {e}")
        return None

def get_latest_telemetry(balloon_id: str) -> Optional[dict]:
    """Retrieves the latest telemetry data for a specific balloon."""
    try:
        response = (
            supabase.table("telemetry")
            .select("*")
            .eq("id", balloon_id)
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
        else:
            return None
    except Exception as e:
        print(f"Error retrieving telemetry data: {e}")
        return None

def check_duplicate(id: str, timestamp: int) -> bool:
    """Checks if a record with the given id and timestamp already exists."""
    try:
        response = (
            supabase.table("telemetry")
            .select("*")
            .eq("id", id)
            .eq("timestamp", timestamp)
            .execute()
        )
        return len(response.data) > 0
    except Exception as e:
        print(f"Error checking for duplicate: {e}")
        return False

def subscribe_to_telemetry_updates(callback: Callable[[dict], None]):
    """Subscribes to real-time updates on the telemetry table."""
    def handle_event(event: str, payload: dict):
        if event in ('INSERT', 'UPDATE'):
            callback(payload['new'])

    supabase.table('telemetry').on('*', handle_event).subscribe()
    print("Subscribed to telemetry updates from Supabase")
