import unittest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from backend.aprs_ingestion import APRSClient, TelemetryData  # Import necessary classes

class TestAPRS(unittest.TestCase):

    @patch('aprs.TCP')
    def test_aprs_ingestion(self, mock_tcp):
        mock_client = MagicMock()
        mock_tcp.return_value = mock_client

        # Simulate receiving APRS frames
        mock_frame_1 = {
            'latitude': 40.7128,
            'longitude': -74.0060,
            'altitude': 1000.0,
            'speed': 50.0,
            'timestamp': 1678886400,
            'from_callsign': 'TEST123'
        }
        mock_frame_2 = {  # Incomplete frame
            'latitude': 34.0522,
            'from_callsign': 'TEST456'
        }
        mock_client.iter.return_value = iter([mock_frame_1, mock_frame_2])

        aprs_client = APRSClient()
        aprs_client.connect()  # This will now use the mocked TCP client

        with patch('backend.aprs_ingestion.store_telemetry') as mock_store, \
             patch('backend.aprs_ingestion.check_duplicate', return_value=False):  # Assuming no duplicates
            aprs_client.receive()

            # Assertions
            mock_store.assert_called_once()  # Should only be called for the complete frame
            # Check the data being stored (adjust based on your parsing logic)
            stored_data = mock_store.call_args[0][0]
            self.assertEqual(stored_data['id'], 'TEST123')
            self.assertEqual(stored_data['lat'], 40.7128)
            self.assertEqual(stored_data['lon'], -74.0060)
            # Add more assertions as needed
