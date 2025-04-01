from datetime import datetime, timezone
import json
import re
from typing import Optional, Union, Any, Literal, Dict, List, Set
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
    AliasChoices
)

from ..models.normalizers import parse_coordinate, normalize_voltage
from ..models.callsign import Callsign

import logging
log = logging.getLogger(__name__)

class ParsedPacket(BaseModel):
    """
    Position and/or telelemtry packets from the device.
    This model is used for parsing and validating the incoming data.
    The model is designed to be flexible with the input format,
    allowing for various aliases and case variations.
    It is the final step before the parsed data is sent to the database.
    """
    # required callsign of the original message transmitter
    callsign: Callsign = Field(..., validation_alias=AliasChoices('callsign', 'call'), description="APRS callsign of the original message transmitter (w/ optional SSID). Note: adding or removing the SSID results in a different callsign that will track separately in the database. This is not a bug, but a feature to allow more devices per callsign.")

    # Location: Required, type is float after validation, but input can vary
    # Float in decimal degrees is the end result
    latitude: float = Field(..., description="Latitude in decimal degrees. Must be a float.", validation_alias=AliasChoices('latitude', 'lat', 'latitude_deg', 'lat_deg', 'lat_dd'))
    longitude: float = Field(..., description="Longitude in decimal degrees. Must be a float.", validation_alias=AliasChoices('longitude', 'lon', 'longitude_deg', 'lon_deg', 'lon_dd'))

    # accuracy of the GPS fix (also know as HDOP or CEP)
    accuracy: Optional[float] = Field(None, description="Accuracy of the GPS fix. Must be a float.", validation_alias=AliasChoices('accuracy', 'acc', 'hdop', 'cep'))

    # altitude in meters
    altitude: Optional[float] = Field(None, description="Altitude in meters. Must be a float.", validation_alias=AliasChoices('altitude', 'alt', 'elevation', 'elev', 'height', 'hgt'))

    # speed in m/s
    speed: Optional[float] = Field(None, description="Speed in m/s. Must be a float.", validation_alias=AliasChoices('speed', 'spd'))

    # course in degrees
    course: Optional[float] = Field(None, description="Course in degrees. Must be a float.", validation_alias=AliasChoices('heading', 'hdg', 'course', 'cse', 'direction', 'dir'), ge=0, le=360)

    # battery voltage in volts
    battery: Optional[float] = Field(
        None,
        # Add common aliases for battery voltage
        validation_alias=AliasChoices('battery_voltage', 'voltage', 'batt_v', 'vbatt', 'battery', 'bat', 'volt', 'v'),
        description="Battery voltage in volts, mV, or scaled volts (V*10).",
    )

    # extra telemtry data (ideally JSON, but allow unparsed compressed data like in APRS telemetry packets)
    extra: Dict[str, Any] = Field(default_factory=dict, description="Extra telemetry data. This is a catch-all for any additional data that doesn't fit into the other fields. It will attempt to be treated as a JSON/dictionary object but will accept unparsed compressed data.", validation_alias=AliasChoices('extra', 'telem', 'telemetry'))

    model_config = {
        "populate_by_name": True,
        # Allows using aliases not just for validation but for population
        # # e.g. Message(call='TEST') works alongside Message(callsign='TEST')

        "extra": "allow",
        # captures any extra fields not defined in the model
        # and adds them to the extra field
    }

    @field_validator('latitude', mode='before')
    @classmethod
    def validate_latitude(cls, value: Any) -> float:
        try:
            return parse_coordinate(value, 'lat')
        except ValueError as e:
            log.error(f"Latitude validation error: {e}")
            raise ValueError(f"Latitude validation error: {e}") from e

    @field_validator('longitude', mode='before')
    @classmethod
    def validate_longitude(cls, value: Any) -> float:
        try:
            return parse_coordinate(value, 'lon')
        except ValueError as e:
            log.error(f"Longitude validation error: {e}")
            raise ValueError(f"Longitude validation error: {e}") from e
    
    @field_validator('battery', mode='before')
    @classmethod
    def validate_battery(cls, value: Any) -> Optional[float]:
        try:
            return normalize_voltage(value)
        except ValueError as e:
            log.error(f"Battery voltage validation error: {e}")
            raise ValueError(f"Battery voltage validation error: {e}") from e
    
    # --- Preprocessing Validator to Collect Extras ---
    @model_validator(mode='before')
    @classmethod
    def collect_extra_fields_revised(cls, data: Any) -> Any:
        """
        Preprocesses input data:
        1. Identifies known fields/aliases for the main model structure.
        2. Separates known fields, sibling extra fields, and the content
           of an explicit 'extra' field (if it's a dict).
        3. Constructs the final 'extra' dictionary giving precedence to
           keys from the explicit 'extra' input, preserving all its keys.
        4. Returns data structured for Pydantic's main parsing pass.
        """
        if not isinstance(data, dict):
            return data # Passthrough non-dict data

        # Identify all possible input keys that map to defined model fields
        # EXCLUDING the 'extra' field itself for this check.
        known_main_field_input_keys = set()
        for field_name, field_info in cls.model_fields.items():
            if field_name == 'extra': # Skip the 'extra' field itself here
                continue

            known_main_field_input_keys.add(field_name) # Field name
            if field_info.alias: # Simple alias
                known_main_field_input_keys.add(field_info.alias)
            val_alias = field_info.validation_alias # Validation alias(es)
            if val_alias:
                if isinstance(val_alias, str): 
                    known_main_field_input_keys.add(val_alias)
                elif isinstance(val_alias, (list, set)): 
                    known_main_field_input_keys.update(val_alias)
                elif hasattr(val_alias, 'choices'): 
                    known_main_field_input_keys.update(val_alias.choices)

        processed_data = {} # Holds data for main model fields
        sibling_extras = {} # Holds extra fields found at the top level
        explicit_extras_content = {} # Holds the content of the input 'extra' field

        # 1. Partition the input data
        for key, value in data.items():
            if key == 'extra':
                # Capture the content if it's a dictionary
                if isinstance(value, dict):
                    explicit_extras_content = value # Keep the whole dict
                else:
                    # Handle non-dict 'extra' as a sibling extra? Or error?
                    # Let's treat it as a sibling extra for now.
                    sibling_extras[key] = value
            elif key in known_main_field_input_keys:
                # Key corresponds to a defined field (not 'extra')
                processed_data[key] = value
            else:
                # It's an unknown key at the top level -> sibling extra
                sibling_extras[key] = value

        # 2. Construct the final 'extra' dictionary (Siblings first, then explicit)
        #    This ensures explicit keys overwrite sibling keys if names clash.
        final_extra_dict = sibling_extras.copy()
        final_extra_dict.update(explicit_extras_content) # explicit_extras overwrite

        # 3. Add the final 'extra' dict to the data Pydantic will process
        processed_data['extra'] = final_extra_dict

        return processed_data

    @model_validator(mode='after')
    def check_at_least_one_identifier(self) -> 'ParsedPacket':
        # This validator runs *after* Pydantic tries to populate the fields
        # using the main names and aliases.
        if self.callsign is None and self.serial is None:
            # Neither canonical field received a value (from its name or alias)

            raise ValueError(
                "Message must contain at least one identifier: "
                "'callsign' (or 'call') or 'serial' (or 'imei')"
            )
        return self

def process_json_msg(raw_data: Union[str, bytes, dict]):
    """
    Parses raw message data (JSON string, bytes, or dict)
    using the Pydantic model.
    """
    try:
        if isinstance(raw_data, (bytes, str)):
            data_dict = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            data_dict = raw_data
        else:
            log.error(f"Invalid data type. Expected str, bytes, or dict, got {type(raw_data)}")
            raise ValueError(f"Invalid data type. Expected str, bytes, or dict, got {type(raw_data)}")

        # --- Key Step: Validate and Normalize Data ---
        # Pydantic handles case variation for keys *if* using populate_by_name=True
        # along with aliases. It will match 'lat', 'Lat', 'LAT' to the 'latitude' field
        # because 'lat' is defined in validation_alias.

        validated_message = ParsedPacket.model_validate(data_dict)
        log.debug(f"Validated ParsedPacket: {validated_message}")

        return validated_message

    except json.JSONDecodeError as e:
        log.error(f"Error decoding JSON: {e}")
        # Handle invalid JSON (e.g., log, send to dead-letter queue)
        return None
    except ValidationError as e:
        log.error(f"Validation Error: {e}")
        # Handle invalid message structure (e.g., missing required fields,
        # type errors, or failed custom validation like the identifier check)
        return None
    except Exception as e:
        log.error(f"An unexpected error occurred during processing: {e}")
        # Handle other potential errors
        return None