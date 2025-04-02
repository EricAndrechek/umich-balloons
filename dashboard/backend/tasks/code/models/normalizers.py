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
import logging

log = logging.getLogger(__name__)

# Altitude -> Meters
ALTITUDE_CONVERSIONS_TO_METERS: Dict[str, float] = {
    "meters": 1.0,
    "m": 1.0,
    "metres": 1.0, # Alternate spelling
    "kilometers": 1000.0,
    "kilometres": 1000.0, # Alternate spelling
    "km": 1000.0,
    "feet": 0.3048,
    "ft": 0.3048,
    "miles": 1609.344, # Use standard definition
    "mi": 1609.344,
    "nautical miles": 1852.0,
    "nm": 1852.0,
    "nmi": 1852.0,
}

# Speed -> Meters per Second
SPEED_CONVERSIONS_TO_MPS: Dict[str, float] = {
    "meters_per_second": 1.0,
    "mps": 1.0,
    "m/s": 1.0,
    "kilometers_per_hour": 1000.0 / 3600.0,
    "km/h": 1000.0 / 3600.0,
    "kph": 1000.0 / 3600.0,
    "miles_per_hour": 1609.344 / 3600.0, # Use standard mile
    "mph": 1609.344 / 3600.0,
    "knots": 1852.0 / 3600.0, # Nautical miles per hour
    "kts": 1852.0 / 3600.0,
    "kt": 1852.0 / 3600.0,
    "feet_per_second": 0.3048,
    "fps": 0.3048,
    "ft/s": 0.3048,
}

def parse_coordinate(value: Union[str, int, float], coord_type: Literal['lat', 'lon']) -> float:
    """
    Parses a coordinate value which can be:
    - float: Assumed to be decimal degrees.
    - int: Assumed to be decimal degrees * 10000.
    - str: Assumed to be Degrees Minutes Seconds (DMS) format.
    Converts valid input to float decimal degrees.
    Raises ValueError for invalid formats or out-of-bounds values.
    """
    max_val = 90.0 if coord_type == 'lat' else 180.0
    min_val = -max_val

    if isinstance(value, float):
        decimal_degrees = value
    elif isinstance(value, int):
        decimal_degrees = float(value) / 10000.0
    elif isinstance(value, str):
        value_str = value.strip()
        # More robust regex to handle various separators (space, °, ', ") and optional direction
        # Allows Deg, Deg Min, Deg Min Sec formats
        pattern = re.compile(r"""
            ^\s* # Optional leading whitespace
            (\d{1,3})                             # Degrees (group 1)
            (?:[:°\s]+                            # Separator (colon, degree symbol or space(s)) REQUIRED
                (\d{1,2})                         # Optional Minutes (group 2)
                (?:[:'\s]+                        # Separator (colon, minute symbol or space(s)) REQUIRED if minutes present
                    (\d{1,2}(?:\.\d+)?)           # Optional Seconds (float) (group 3)
                    (?:["\s]*)?                   # Optional trailing separator/symbol
                )?                                # End optional Seconds group
            )?                                    # End optional Minutes group
            \s* # Optional intermediate whitespace
            ([NSEWnsew])?                         # Optional Direction (N,S,E,W, case-insensitive) (group 4)
            \s*$                                  # Optional trailing whitespace
        """, re.VERBOSE | re.IGNORECASE) # Ignore case for direction letters

        match = pattern.match(value_str)
        if not match:
            # Try parsing as simple float string as fallback before failing
            try:
                decimal_degrees = float(value_str)
            except ValueError:
                 raise ValueError(f"Invalid DMS or float string format: '{value_str}'")

        else:
            # Parsed DMS components
            deg_str, min_str, sec_str, direction = match.groups()

            degrees = float(deg_str)
            minutes = float(min_str) if min_str else 0.0
            seconds = float(sec_str) if sec_str else 0.0

            if minutes >= 60 or seconds >= 60:
                raise ValueError(f"Invalid DMS values (minutes/seconds >= 60): '{value_str}'")

            decimal_degrees = degrees + minutes / 60.0 + seconds / 3600.0

            if direction:
                direction = direction.upper()
                if coord_type == 'lat' and direction not in ('N', 'S'):
                     raise ValueError(f"Invalid direction '{direction}' for latitude")
                if coord_type == 'lon' and direction not in ('E', 'W'):
                     raise ValueError(f"Invalid direction '{direction}' for longitude")

                if direction in ('S', 'W'):
                    decimal_degrees *= -1
            # Basic validation if direction is missing (e.g., assume positive for N/E)
            # More robust checking might be needed depending on source conventions
            elif decimal_degrees < 0 and coord_type == 'lat': # Implicit S
                 pass # Allow negative degrees without S
            elif decimal_degrees < 0 and coord_type == 'lon': # Implicit W
                 pass # Allow negative degrees without W


    else:
        raise ValueError(f"Invalid type for coordinate: {type(value)}")

    # Final bounds check
    if not (min_val <= decimal_degrees <= max_val):
        raise ValueError(f"Coordinate {decimal_degrees:.6f} out of bounds ({min_val} to {max_val})")

    log.debug(f"Parsed {coord_type.upper()} coordinate: {decimal_degrees:.6f} from value '{value}'")
    return decimal_degrees

def normalize_voltage(value: Any) -> Optional[float]:
    """
    Normalizes battery voltage input to Volts (float).
    Handles input as:
    - mV (e.g., 3892, 3892.17) -> Assumed if value > 1000
    - Volts (e.g., 3, 3.8, 3.769) -> Assumed if value < 20 or is float
    - Scaled Volts (V*10, e.g., 38, 42) -> Assumed for integers between 20 and 60 (heuristic!)
    Returns float in Volts or None if input is None.
    Raises ValueError for invalid types or negative values.
    """
    if value is None:
        return None

    if not isinstance(value, (int, float)):
        raise ValueError(f"Invalid type for voltage: Expected int or float, got {type(value)}")

    v_float = float(value) # Use float for comparisons

    if v_float < 0:
        raise ValueError("Voltage cannot be negative")

    # 1. Check for likely Millivolts (mV)
    # Using 1000 as a threshold is generally safe.
    if v_float > 1000.0:
        log.debug(f"Assuming voltage '{v_float}' is in mV. Converting to Volts: {v_float / 1000.0:.2f}V")
        return v_float / 1000.0

    # 2. Heuristic: Check for integers likely representing Volts * 10
    # Common for 3.0-4.2V batteries showing as 30-42.
    # This is ambiguous (24 could be 2.4V or 24V). We prioritize V*10 for ints in this range.
    # Adjust range [20, 60] based on your expected device voltage ranges if needed.
    if isinstance(value, int) and 20 <= value <= 60:
        # Issue a warning as this is a heuristic guess
        log.warning(f"Assuming integer voltage '{value}' is scaled (V*10). Interpreting as {value / 10.0:.2f}V.")
        return float(value) / 10.0

    # 3. Assume Direct Volts
    # Covers:
    # - Floats (e.g., 3.8, 12.1, or even 38.5 which wouldn't match rule 2)
    # - Integers below the V*10 range (e.g., 3, 12)
    # - Integers above the V*10 range but below the mV range (e.g., 100)
    # - Integers within the V*10 range *if* the heuristic (rule 2) is removed/modified.
    if v_float > 60.0: # If it wasn't mV (>1000) but is still high (e.g. 100)
         log.warning(f"Voltage '{v_float}' seems high but not mV range. Interpreting directly as Volts.")

    log.debug(f"Returning normalized voltage: {v_float:.2f}V")
    return v_float # Return as float

