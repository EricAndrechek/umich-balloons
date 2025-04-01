from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, Union
import uuid

import logging
log = logging.getLogger(__name__)

class RawMessage(BaseModel):
    """Model for raw Redis messages between services."""
    sender: Optional[str] = Field(..., description="ID of device that relayed message to us. Defaults to IP address for HTTP events, client ID for MQTT events, and nothing for APRS events.")
    payload: Union[dict, str, bytes] = Field(..., description="Raw message payload. This is the raw data received from the device.")
    timestamp: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of the message. Should be as close to the original timestamp as possible, falling back to the current time if not available.")