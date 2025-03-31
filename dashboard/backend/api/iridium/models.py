from datetime import datetime, timezone
from pydantic import BaseModel, Field

import logging
log = logging.getLogger(__name__)

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
    JWT: str = Field(..., description="JWT token for authentication")