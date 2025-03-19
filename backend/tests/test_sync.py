import unittest
from unittest.mock import patch, MagicMock
import json
from backend.database import subscribe_to_telemetry_updates
# Assuming the publish_update function is in main.py and needs to be imported or mocked
# from backend.main import publish_update 

class TestSync(unittest.TestCase):

    @patch('backend.database.supabase.table')
    def test_subscribe_to_telemetry_updates(self, mock_table):
        mock_on = MagicMock()
        mock_subscribe = MagicMock()
        mock_table().on.return_value = mock_on
        mock_on.return_value.subscribe = mock_subscribe

        # Create a mock callback function
        mock_callback = MagicMock()

        # Call the function to test
        subscribe_to_telemetry_updates(mock_callback)

        # Assertions
        mock_table.assert_called_once_with('telemetry')
        mock_table().on.assert_called_once_with('*', unittest.mock.ANY)
        mock_subscribe.assert_called_once()

        # Simulate an event and check if the callback is called
        event_handler = mock_on.call_args[0][1]  # Get the event handler function
        test_payload = {'new': {'id': 'test_balloon', 'lat': 40.7128, 'lon': -74.0060}}
        event_handler('INSERT', test_payload)
        mock_callback.assert_called_once_with(test_payload['new'])

        # Test with an update event
        mock_callback.reset_mock()
        event_handler('UPDATE', test_payload)
        mock_callback.assert_called_once_with(test_payload['new'])

        # Test with a delete event (callback should not be called)
        mock_callback.reset_mock()
        event_handler('DELETE', {'old': test_payload['new']})
        mock_callback.assert_not_called()

    # To fully test the sync, you'd need to also check the MQTT publishing
    # This might require a more complex test setup or mocking of the MQTT client
    # For example:
    # @patch('backend.main.client.publish')
    # def test_sync_end_to_end(self, mock_publish):
    #     # ... set up mocks and call functions ...
    #     # ... assert mock_publish.called ...
    #     pass
