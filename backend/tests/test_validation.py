import unittest
from pydantic import ValidationError
from backend.ingestion import TelemetryData  # Assuming TelemetryData is in ingestion.py
from backend.database import check_duplicate  # Assuming check_duplicate is in database.py

class TestValidation(unittest.TestCase):

    def test_valid_telemetry_data(self):
        data = {
            "id": "test_balloon",
            "lat": 40.7128,
            "lon": -74.0060,
            "alt": 1000.0,
            "speed": 50.0,
            "source": "LoRa",
            "timestamp": 1678886400
        }
        try:
            TelemetryData(**data)
        except ValidationError:
            self.fail("TelemetryData validation raised ValidationError unexpectedly!")

    def test_invalid_latitude(self):
        data = {
            "id": "test_balloon",
            "lat": 91.0,  # Invalid latitude
            "lon": -74.0060,
            "alt": 1000.0,
            "speed": 50.0,
            "source": "LoRa",
            "timestamp": 1678886400
        }
        with self.assertRaises(ValidationError):
            TelemetryData(**data)

    def test_invalid_longitude(self):
        data = {
            "id": "test_balloon",
            "lat": 40.7128,
            "lon": -181.0,  # Invalid longitude
            "alt": 1000.0,
            "speed": 50.0,
            "source": "LoRa",
            "timestamp": 1678886400
        }
        with self.assertRaises(ValidationError):
            TelemetryData(**data)

    def test_check_duplicate(self):
        from unittest.mock import patch

        with patch('backend.database.supabase.table') as mock_table:
            mock_table().select().eq().eq().execute.return_value.data = []
            self.assertFalse(check_duplicate("non_existent_id", 1234567890))
            mock_table().select().eq().eq().execute.return_value.data = [{"id": "existing_id", "timestamp": 9876543210}]
            self.assertTrue(check_duplicate("existing_id", 9876543210))

if __name__ == '__main__':
    unittest.main()
