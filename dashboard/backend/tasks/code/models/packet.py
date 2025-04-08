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
    AliasChoices,
    ValidationInfo
)

try:
    from ..models.normalizers import parse_coordinate, normalize_voltage, get_precision_radius
except ImportError:
    from models.normalizers import parse_coordinate, normalize_voltage, get_precision_radius
try:
    from ..models.callsign import Callsign
except ImportError:
    from models.callsign import Callsign

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
    callsign: Callsign = Field(..., validation_alias=AliasChoices('callsign', 'call', 'from'), description="APRS callsign of the original message transmitter (w/ optional SSID). Note: adding or removing the SSID results in a different callsign that will track separately in the database. This is not a bug, but a feature to allow more devices per callsign.")

    # Location: Required, type is float after validation, but input can vary
    # Float in decimal degrees is the end result
    latitude: float = Field(..., description="Latitude in decimal degrees. Must be a float.", validation_alias=AliasChoices('latitude', 'lat', 'latitude_deg', 'lat_deg', 'lat_dd'))
    longitude: float = Field(..., description="Longitude in decimal degrees. Must be a float.", validation_alias=AliasChoices('longitude', 'lon', 'longitude_deg', 'lon_deg', 'lon_dd'))

    # accuracy of the GPS fix (also know as CEP) in meters
    accuracy: Optional[float] = Field(None, description="Accuracy of the GPS fix. Must be a float.", validation_alias=AliasChoices('accuracy', 'acc', 'cep', 'cep_m', 'cep_meters', 'cep_accuracy', 'accuracy_m', 'accuracy_meters')
    )

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

    symbol: Optional[str] = Field(None, description="Symbol for the position. See APRS symbol table for valid symbols.", validation_alias=AliasChoices('symbol', 'sym'))

    # extra telemtry data (ideally JSON, but allow unparsed compressed data like in APRS telemetry packets)
    extra: Dict[str, Any] = Field(default_factory=dict, description="Extra telemetry data. This is a catch-all for any additional data that doesn't fit into the other fields. It will attempt to be treated as a JSON/dictionary object but will accept unparsed compressed data.", validation_alias=AliasChoices('extra', 'telem', 'telemetry'))

    # --- Optional Fields ---
    data_time: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the message. Should be as close to the original timestamp as possible, falling back to the current time if not available.",
        validation_alias=AliasChoices('timestamp', 'time', 'datetime', 'dt', 'date_time', 'data_time')
    )

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

def process_json_msg(raw_data: Union[str, bytes, dict], source_type: Optional[Literal['APRS', 'LoRa', 'Iridium']] = None, iridium_latitude=None, iridium_longitude=None, iridium_cep=None) -> ParsedPacket:
    """
    Parses raw message data (JSON string, bytes, or dict)
    using the Pydantic model.
    """
    if isinstance(raw_data, (bytes, str)):
        data_dict = json.loads(raw_data)
    elif isinstance(raw_data, dict):
        data_dict = raw_data
    else:
        log.error(f"Invalid data type. Expected str, bytes, or dict, got {type(raw_data)}")
        raise ValueError(f"Invalid data type. Expected str, bytes, or dict, got {type(raw_data)}")

    # usually APRS specific, but not strictly
    # fix symbol_table and symbol_id/symbol keys
    if 'symbol_table' in data_dict and 'symbol_id' in data_dict:
        data_dict['symbol'] = f"{data_dict['symbol_table']}{data_dict['symbol_id']}"
        del data_dict['symbol_table']
        del data_dict['symbol_id']
    elif 'symbol' in data_dict:
        if len(data_dict['symbol']) == 1:
            if 'symbol_table' in data_dict:
                data_dict['symbol'] = f"{data_dict['symbol_table']}{data_dict['symbol']}"
                del data_dict['symbol_table']
            elif 'symbol_id' in data_dict:
                data_dict['symbol'] = f"{data_dict['symbol']}{data_dict['symbol_id']}"
                del data_dict['symbol_id']
        elif len(data_dict['symbol']) != 2:
            log.warning(f"Invalid symbol format: {data_dict['symbol']}. Expected 1 or 2 characters.")
            data_dict['symbol'] = data_dict['symbol'][:2]

    # is APRS specific, unique ambiguity
    if source_type == 'APRS' and 'posambiguity' in data_dict:
        data_dict['cep'] = get_precision_radius('APRS', ambiguity=data_dict['posambiguity'])
        del data_dict['posambiguity']

    # --- Key Step: Validate and Normalize Data ---
    # Pydantic handles case variation for keys *if* using populate_by_name=True
    # along with aliases. It will match 'lat', 'Lat', 'LAT' to the 'latitude' field
    # because 'lat' is defined in validation_alias.

    using_iridium = False

    try:
        validated_message = ParsedPacket.model_validate(data_dict)
    # catch iridium no lat/lon case
    # where lat/lon are missing but iridium_latitude/longitude are present
    except ValidationError as e:
        if source_type == 'Iridium' and iridium_latitude is not None and iridium_longitude is not None and iridium_cep is not None:
            # add iridium lat/lon to the validated message
            data_dict['latitude'] = iridium_latitude
            data_dict['longitude'] = iridium_longitude
            data_dict['accuracy'] = iridium_cep
            # re-validate with the new data
            validated_message = ParsedPacket.model_validate(data_dict)
            using_iridium = True
        else:
            raise e
    
    # if lora/iridium (and not using iridium satellite derived lat/lon) we can calculate cep from precision of decimals
    # for example:  82.1234 has an ambiguity of 0
    #               82.123  has an ambiguity of 1
    #               82.12   has an ambiguity of 2
    #               82.1    has an ambiguity of 3
    #               82.     has an ambiguity of 4
    if source_type in ['LoRa', 'Iridium'] and not using_iridium and validated_message.accuracy is None:
        # calculate the precision of the latitude and longitude
        lat_precision = 0
        lon_precision = 0
        if '.' in str(validated_message.latitude):
            lat_precision = max(4 - len(str(validated_message.latitude).split('.')[1]), 0)
        else:
            lat_precision = max(6 - len(str(validated_message.latitude).split('.')[0]), 4)
        if '.' in str(validated_message.longitude):
            lon_precision = max(4 - len(str(validated_message.longitude).split('.')[1]), 0)
        else:
            lon_precision = max(6 - len(str(validated_message.longitude).split('.')[0]), 4)
        # calculate the cep from the precision
        # source type is always LoRa here (even for Iridium)
        # since Iridium packet type here is the same as LoRa
        # and not Iridium's satellite derived lat/lon type
        cep = get_precision_radius('LoRa', ambiguity=max(lat_precision, lon_precision))
        # set the accuracy to the cep
        validated_message.accuracy = cep

        # now if Iridium, we can see if the Iridium satellite derived lat/lon is more accurate
        if source_type == 'Iridium' and iridium_latitude is not None and iridium_longitude is not None and iridium_cep is not None:
            # if the Iridium CEP is less than the calculated cep, use it
            if iridium_cep < validated_message.accuracy:
                validated_message.accuracy = iridium_cep
                validated_message.latitude = iridium_latitude
                validated_message.longitude = iridium_longitude

    # fix lora/iridium specific conversions:
    # altitude was in hectometer (1 = 100m)
    # speed was knots rounded to nearest whole number
    # lora and iridium specific altitude and speed conversion
    if source_type == 'LoRa' or source_type == 'Iridium':
        # convert altitude from hectometer to meters
        if validated_message.altitude is not None:
            validated_message.altitude = validated_message.altitude * 100
    # APRS gives altitude in feet
    elif source_type == 'APRS':
        # convert altitude from feet to meters
        if validated_message.altitude is not None:
            validated_message.altitude = validated_message.altitude * 0.3048
    
    # all sources give speed in knots
    # convert speed from knots to meters per second
    if validated_message.speed is not None:
        validated_message.speed = validated_message.speed * 0.51444444444

    # final check - if lat or lon are 0, invalidate the message
    if validated_message.latitude == 0 or validated_message.longitude == 0:
        raise ValueError("Latitude and Longitude cannot be 0. Apologies if you really are at 0,0, but too many people send 0,0 when they don't have GPS lock.")

    log.debug(f"Validated ParsedPacket: {validated_message}")
    return validated_message