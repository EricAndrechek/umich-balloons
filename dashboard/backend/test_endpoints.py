# test_endpoints.py
import pytest
import requests
import json
from datetime import datetime, timezone
import jwt
import os
from dotenv import load_dotenv
from config import settings  # import settings from config!

# Load environment variables
load_dotenv()

# --- Configuration (adjust as needed) ---
BASE_URL = "http://localhost:8000"  # Or your deployment URL
ROCKBLOCK_IMEI = "300434063999999"  # replace with a test imei


# Generate a valid JWT for testing (using the RockBLOCK public key)
# In a real scenario, you wouldn't do this; RockBLOCK would send the JWT.
def generate_test_jwt(payload, algorithm="RS256"):
    """Generates a test JWT signed with the RockBLOCK private key (for testing)."""
    # IMPORTANT: For testing, you need the *PRIVATE* key corresponding to the
    # public key you configured.  DO NOT USE A REAL PRIVATE KEY IN PRODUCTION.
    # This is purely for simulating a valid RockBLOCK JWT in your tests.
    # Replace this with your *TEST* private key.
    with open("my_private_key", "r") as f:
        private_key = f.read()
        if not private_key:
            raise ValueError("TEST_PRIVATE_KEY environment variable not set.")
        encoded_jwt = jwt.encode(payload, private_key, algorithm=algorithm)
        return encoded_jwt

# test_endpoints.py
import pytest
import requests
import json
from datetime import datetime, timezone
import jwt
from dotenv import load_dotenv
from config import settings

# Load environment variables
load_dotenv()

# --- Configuration (adjust as needed) ---
BASE_URL = "http://localhost:8000"  # Or your deployment URL
ROCKBLOCK_IMEI = "test_imei"  # replace with a test imei


# Generate a valid JWT for testing (using the RockBLOCK public key)
def generate_test_jwt(payload, algorithm="RS256"):
    private_key = os.environ.get("TEST_PRIVATE_KEY")
    if not private_key:
        raise ValueError("TEST_PRIVATE_KEY environment variable not set.")
    encoded_jwt = jwt.encode(payload, private_key, algorithm=algorithm)
    return encoded_jwt


# --- Helper Function ---
def to_json_serializable(data: dict) -> dict:
    """Converts datetime objects in a dictionary to ISO 8601 strings."""
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


# --- Test Cases ---


def test_iridium_webhook_valid():
    """Tests the Iridium webhook with valid data and a valid JWT."""
    jwt_payload = {"iss": "RockBLOCK"}
    token = generate_test_jwt(jwt_payload, algorithm=settings.jwt_algorithm)

    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "imei": ROCKBLOCK_IMEI,
        "momsn": 12345,
        "transmit_time": "2024-10-27T14:30:00Z",
        "iridium_latitude": 34.05,
        "iridium_longitude": -118.24,
        "iridium_cep": 10,
        "data": "eyJhbHRpdHVkZSI6IDEwMC41LCAic3BlZWQiOiAyNS4zLCAiaGVhZGluZyI6IDkwLjAsICJiYXR0ZXJ5IjogNC44fQ==",  # base64 encoded
        "data_time": datetime(2024, 10, 27, 14, 30, 0, tzinfo=timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/iridium", headers=headers, json=data)
    assert response.status_code == 200
    assert "message" in response.json()
    assert "Iridium data processed successfully" in response.json()["message"]


def test_iridium_webhook_invalid_jwt():
    """Tests the Iridium webhook with an invalid JWT."""
    headers = {"Authorization": "Bearer invalidtoken"}
    data = {
        "imei": ROCKBLOCK_IMEI,
        "momsn": 12345,
        "transmit_time": "2023-10-27T14:30:00Z",
        "iridium_latitude": 34.05,
        "iridium_longitude": -118.24,
        "iridium_cep": 10,
        "data": "SGVsbG8gV29ybGQh",  # "Hello World!" in base64
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/iridium", headers=headers, json=data)
    assert response.status_code == 403


def test_iridium_webhook_invalid_lat_lon():
    """Tests the Iridium webhook with invalid latitude and longitude."""
    jwt_payload = {"iss": "RockBLOCK"}
    token = generate_test_jwt(jwt_payload, algorithm=settings.jwt_algorithm)

    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "imei": ROCKBLOCK_IMEI,
        "momsn": 12345,
        "transmit_time": "2023-10-27T14:30:00Z",
        "iridium_latitude": 91.0,  # Invalid latitude
        "iridium_longitude": -181.0,  # Invalid longitude
        "iridium_cep": 10,
        "data": "SGVsbG8gV29ybGQh",
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/iridium", headers=headers, json=data)

    assert response.status_code == 422
    assert (
        "iridium latitude/longitude must be between -180 and 180"
        in response.text.lower()
    )


def test_plain_json_webhook_valid():
    """Tests the plain JSON webhook with valid data."""
    data = {
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 50.0,
        "callsign": "TESTCALL",  # CORRECT - Top-level callsign
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/json", json=data)
    assert response.status_code == 200
    assert "message" in response.json()
    assert "JSON data processed successfully" in response.json()["message"]


def test_plain_json_webhook_missing_callsign():
    """Tests the plain JSON webhook with missing callsign."""
    data = {
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 50.0,
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/json", json=data)
    assert response.status_code == 422
    assert "callsign" in response.text.lower()


def test_plain_json_webhook_invalid_lat_lon():
    """Tests with invalid latitude/longitude."""
    data = {
        "latitude": 91.0,  # INVALID
        "longitude": -181.0,  # INVALID
        "altitude": 50.0,
        "callsign": "TESTCALL",
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/json", json=data)
    assert response.status_code == 422
    assert "latitude/longitude must be between -180 and 180" in response.text.lower()


def test_mqtt_webhook_valid():
    """Tests the MQTT webhook with valid data."""
    data = {
        "latitude": 51.5074,
        "longitude": -0.1278,
        "altitude": 25.0,
        "topic": "test/topic",
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/mqtt", json=data)
    assert response.status_code == 200
    assert "MQTT data processed successfully" in response.json()["message"]


def test_mqtt_webhook_missing_lat_lon():
    """Tests the MQTT webhook with missing latitude and longitude."""
    data = {
        "altitude": 75.0,
        "topic": "another/topic",
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/mqtt", json=data)
    assert response.status_code == 422


def test_mqtt_webhook_invalid_lat_lon():
    """Tests the MQTT webhook with invalid latitude and longitude."""
    data = {
        "latitude": -190.0,  # Invalid
        "longitude": 200.0,  # Invalid
        "altitude": 100.0,
        "topic": "invalid/topic",
        "data_time": datetime.now(timezone.utc),
    }
    data = to_json_serializable(data)
    response = requests.post(f"{BASE_URL}/webhook/mqtt", json=data)
    assert response.status_code == 422
