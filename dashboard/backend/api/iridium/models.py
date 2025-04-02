from datetime import datetime, timezone
from typing import Optional, Union, Any, Literal, Dict, List, Set
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
    AliasChoices
)

from datetime import datetime

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
    transmit_time: datetime = Field(..., description="Transmit time like 25-03-26 23:45:44 (YY-MM-DD HH:MM:SS)")
    JWT: str = Field(..., description="JWT token for authentication")

    @field_validator('transmit_time', mode='before')
    @classmethod
    def validate_transmit_time(cls, value: Any) -> datetime:
        """
        Iridium sends us the transmit time in a specific format (YY-MM-DD HH:MM:SS), which we can't change
        So, let's fix it ourselves here
        """
        if isinstance(value, str):
            try:
                # Convert the string to a datetime object
                return datetime.strptime(value, "%y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError as e:
                log.error(f"Invalid transmit_time format: {value}. Error: {e}")
                raise e
        elif isinstance(value, datetime):
            # If it's already a datetime object, just return it
            return value
        else:
            raise ValueError(f"Invalid transmit_time type: {type(value)}. Expected str or datetime.")
