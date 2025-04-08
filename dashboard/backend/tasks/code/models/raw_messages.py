from datetime import datetime, timezone
import base64
import binascii
from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Optional, Union
import uuid

import logging
log = logging.getLogger(__name__)

class RawMessage(BaseModel):
    """Model for raw Redis messages between services."""
    sender: Optional[str] = Field(None, description="ID of device that relayed message to us. Defaults to IP address for HTTP events, client ID for MQTT events, and nothing for APRS events.")

    payload: Union[dict, str, bytes] = Field(..., description="Raw message payload. This is the raw data received from the device.")

    timestamp: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of the message. Should be as close to the original timestamp as possible, falling back to the current time if not available.")

    ingest_method: Optional[str] = Field(None, description="Method of ingestion. This is the method used to send the message to the server. Can be HTTP, MQTT, or other.")

    @field_validator('payload', mode='before')
    @classmethod
    def decode_base64_if_needed(cls, v):
        # This validator runs *before* Pydantic tries to validate the type.
        # 'v' here is the raw value from the input data (e.g., JSON dict).

        if isinstance(v, str):
            try:
                # Attempt to decode the string as Base64
                # Use validate=True for stricter validation if needed,
                # requires Python 3.x+ and checks if the input only contains
                # valid base64 characters.
                # We need to encode the string back to ascii/utf-8 before decoding
                # as b64decode expects bytes.
                decoded_bytes = base64.b64decode(v.encode('utf-8'), validate=True)
                return decoded_bytes # Return the bytes if decoding is successful
            except (binascii.Error, ValueError) as e:
                # If decoding fails, assume it's a regular string.
                # binascii.Error is the specific error for invalid Base64 data.
                # ValueError can occur for padding issues depending on implementation.
                return v # Return the original string
        elif isinstance(v, bytes):
             # If it's already bytes (e.g., passed directly in Python, not from JSON)
             return v
        else:
             # Handle other unexpected types if necessary, or let Pydantic handle them
             return v

class IridiumMessage(BaseModel):
    """Model for Iridium message."""
    momsn: int = Field(..., description="Message ID")
    imei: str = Field(..., description="IMEI of the device")
    data: str = Field(..., description="Message data as a hex string")
    serial: int = Field(..., description="Serial number of the device")
    device_type: str = Field(..., description="Type of device")
    iridium_latitude: float = Field(..., description="Latitude in degrees")
    iridium_longitude: float = Field(..., description="Longitude in degrees")
    iridium_cep: float = Field(..., description="CEP in km input, ourput in m")
    transmit_time: str = Field(..., description="Transmit time like 25-03-26 23:45:44")

    @field_validator('iridium_cep', mode='before')
    @classmethod
    def convert_iridium_cep(cls, v):
        # Convert the input value from km to meters
        if isinstance(v, (int, float)):
            return v * 1000
        else:
            raise ValueError(f"Invalid type for iridium_cep: {type(v)}. Expected int or float.")