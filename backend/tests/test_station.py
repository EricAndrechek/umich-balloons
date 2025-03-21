import unittest
from unittest.mock import patch, MagicMock
import json
from pydantic import ValidationError
from backend.station_ingestion import on_connect, on_message, StationData  # Import necessary functions and classes

class TestStation(unittest.TestCase):

    @patch('paho.mqtt.client.Client')
    def test_on_connect(self, mock_client):
        mock_client_instance = mock_client.return_value
        on_connect(mock_client_instance, None, None, 0)
        mock_client_instance.subscribe.assert_called_once_with("stations/data")

    @patch('paho.mqtt.client.Client')
    def test_on_message_valid_data(self, mock_client):
        mock_client_instance = mock_client.return_value
        valid_payload = {
            "station_id": "Station-123",
            "latitude": 34.0522,
            "longitude": -118.2437,
            "status": "Active",
            "timestamp": 1678886400
        }
        mock_msg = MagicMock()
        mock_msg.payload = json.dumps(valid_payload).encode()
        mock_msg.topic = "stations/data"

        with patch('backend.station_ingestion.supabase.table') as mock_table:
            mock_insert = MagicMock()
            mock_table().insert.return_value = mock_insert
            mock_insert.return_value.execute.return_value.data = [valid_payload]  # Simulate successful insert
            on_message(mock_client_instance, None, mock_msg)
            mock_table().insert.assert_called_once_with(valid_payload)

    @patch('paho.mqtt.client.Client')
    def test_on_message_invalid_data(self, mock_client):
        mock_client_instance = mock_client.return_value
        invalid_payload = {
            "station_id": "Station-123",
            "latitude": 91.0,  # Invalid latitude
            "longitude": -118.2437,
            "status": "Active",
            "timestamp": 1678886400
        }
        mock_msg = MagicMock()
        mock_msg.payload = json.dumps(invalid_payload).encode()
        mock_msg.topic = "stations/data"

        with patch('backend.station_ingestion.supabase.table') as mock_table:
            on_message(mock_client_instance, None, mock_msg)
            mock_table().insert.assert_not_called()

    @patch('paho.mqtt.client.Client')
    def test_on_message_json_decode_error(self, mock_client):
        mock_client_instance = mock_client.return_value
        mock_msg = MagicMock()
        mock_msg.payload = b"invalid json"
        mock_msg.topic = "stations/data"

        with patch('backend.station_ingestion.supabase.table') as mock_table:
            on_message(mock_client_instance, None, mock_msg)
            mock_table().insert.assert_not_called()

if __name__ == '__main__':
    unittest.main()
