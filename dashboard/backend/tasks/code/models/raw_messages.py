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

class IridiumMessage(BaseModel):
    """Model for Iridium message."""
    momsn: int = Field(..., description="Message ID")
    imei: str = Field(..., description="IMEI of the device")
    data: str = Field(..., description="Message data as a hex string")
    serial: int = Field(..., description="Serial number of the device")
    device_type: str = Field(..., description="Type of device")
    iridium_latitude: float = Field(..., description="Latitude in degrees")
    iridium_longitude: float = Field(..., description="Longitude in degrees")
    iridium_cep: float = Field(..., description="CEP in meters")
    transmit_time: str = Field(..., description="Transmit time like 25-03-26 23:45:44")
