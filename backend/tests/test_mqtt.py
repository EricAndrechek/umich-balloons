import unittest
from unittest.mock import patch, MagicMock
import json
from pydantic import ValidationError
from backend.ingestion import on_connect, on_message, TelemetryData  # Import necessary functions and classes

class TestMQTT(unittest.TestCase):

    @patch('paho.mqtt.client.Client')
    def test_on_connect(self, mock_client):
        mock_client_instance = mock_client.return_value
        on_connect(mock_client_instance, None, None, 0)
        mock_client_instance.subscribe.assert_called_once_with("balloons/telemetry")

    @patch('paho.mqtt.client.Client')
    def test_on_message_valid_data(self, mock_client):
        mock_client_instance = mock_client.return_value
        valid_payload = {
            "id": "test_balloon",
            "lat": 40.7128,
            "lon": -74.0060,
            "alt": 1000.0,
            "speed": 50.0,
            "source": "LoRa",
            "timestamp": 1678886400
        }
        mock_msg = MagicMock()
        mock_msg.payload = json.dumps(valid_payload).encode()
        mock_msg.topic = "balloons/telemetry"

        with patch('backend.ingestion.store_telemetry') as mock_store:
            on_message(mock_client_instance, None, mock_msg)
            mock_store.assert_called_once()
            # You might want to assert the arguments passed to mock_store

    @patch('paho.mqtt.client.Client')
    def test_on_message_invalid_data(self, mock_client):
        mock_client_instance = mock_client.return_value
        invalid_payload = {
            "id": "test_balloon",
            "lat": 91.0,  # Invalid latitude
            "lon": -74.0060,
            "alt": 1000.0,
            "speed": 50.0,
            "source": "LoRa",
            "timestamp": 1678886400
        }
        mock_msg = MagicMock()
        mock_msg.payload = json.dumps(invalid_payload).encode()
        mock_msg.topic = "balloons/telemetry"

        with patch('backend.ingestion.store_telemetry') as mock_store:
            on_message(mock_client_instance, None, mock_msg)
            mock_store.assert_not_called()

    @patch('paho.mqtt.client.Client')
    def test_on_message_json_decode_error(self, mock_client):
        mock_client_instance = mock_client.return_value
        mock_msg = MagicMock()
        mock_msg.payload = b"invalid json"
        mock_msg.topic = "balloons/telemetry"

        with patch('backend.ingestion.store_telemetry') as mock_store:
            on_message(mock_client_instance, None, mock_msg)
            mock_store.assert_not_called()

if __name__ == '__main__':
    unittest.main()
