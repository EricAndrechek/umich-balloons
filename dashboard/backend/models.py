# models.py
from datetime import datetime, timezone
import uuid
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, field_validator, Field

# from geojson import Point, Feature  # No longer directly use geojson
from dataclasses import dataclass, field


class RawMessage(BaseModel):
    """Represents a raw message received from any source."""

    source: str
    payload_id: int
    raw_data: str
    data_time: Optional[datetime] = None
    source_id: Optional[str] = None


class BaseTelemetryData(BaseModel):
    """Base model for common telemetry data."""

    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    battery: Optional[float] = None
    data_time: Optional[datetime] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("latitude", "longitude")
    @classmethod
    def check_lat_lon(cls, value):
        """Validates that latitude and longitude are within the valid range."""
        if not (-180 <= value <= 180):
            raise ValueError("Latitude/Longitude must be between -180 and 180")
        return value

    model_config = dict(extra="allow")  # Allow extra fields


class IridiumTelemetryData(BaseTelemetryData):
    """Model for Iridium-specific telemetry data."""

    model_config = dict(extra="allow")
    pass


class PlainJsonTelemetryData(BaseTelemetryData):
    """Model for telemetry data from plain JSON POST requests."""

    callsign: str  # Add callsign as a required field
    model_config = dict(extra="allow")


class MqttTelemetryData(BaseTelemetryData):
    """Model for telemetry data from MQTT."""

    topic: str
    model_config = dict(extra="allow")


class IridiumRockblockData(BaseModel):
    """Model for the data received in the Rockblock Iridium POST request."""

    imei: str
    momsn: int
    transmit_time: str
    iridium_latitude: float
    iridium_longitude: float
    iridium_cep: float
    data: str
    data_time: datetime

    @field_validator("iridium_latitude", "iridium_longitude")
    @classmethod
    def check_lat_lon_iridium(cls, value):
        """Validates that Iridium latitude and longitude are within the valid range."""
        if not (-180 <= value <= 180):
            raise ValueError("Iridium Latitude/Longitude must be between -180 and 180")
        return value

    model_config = dict(extra="allow")


@dataclass
class Telemetry:
    """Represents a telemetry data point, designed for easy creation and database upload."""

    payload_id: int
    position: str  # Change type to str (for WKT/EWKT)
    sources: List[str] = field(default_factory=list)
    altitude: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    battery: Optional[float] = None
    extra: Dict = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_time: Optional[datetime] = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        """Converts the Telemetry object to a dictionary for database insertion."""
        data = {
            "id": str(self.id),
            "payload_id": self.payload_id,
            "position": self.position,
            "sources": self.sources,
            "altitude": self.altitude,
            "speed": self.speed,
            "heading": self.heading,
            "battery": self.battery,
            "extra": self.extra,
            "last_updated": self.last_updated.isoformat(),
            "event_time": (
                self.event_time.isoformat()
                if self.event_time is not None
                else datetime.now(timezone.utc).isoformat()
            ),
        }
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_telemetry_data(
        cls, payload_id: int, raw_message_id: str, data: BaseTelemetryData
    ):
        """Creates a Telemetry object from a BaseTelemetryData object."""
        # Create WKT/EWKT string directly
        position = f"POINT({data.longitude} {data.latitude})"
        # Use data_time if provided, else default to now
        event_time = data.data_time if data.data_time else datetime.now(timezone.utc)
        return cls(
            payload_id=payload_id,
            position=position,
            sources=[raw_message_id],
            altitude=data.altitude,
            speed=data.speed,
            heading=data.heading,
            battery=data.battery,
            extra=data.extra,
            event_time=event_time,
        )
