# pydantic custom type for callsigns to validate the format

from pydantic import BaseModel, Field, constr
from typing import Optional, Union

import logging
log = logging.getLogger(__name__)

class Callsign(str):
    """
    Pydantic custom type for callsigns to validate the format.
    Callsigns must be 1-6 characters long, alphanumeric, and start with a letter.
    """
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError('Calls sign must be a string')
        if len(value) > 9:
            raise ValueError('Calls sign must be 3-6 characters long with an optional SSID')
        if not value[0].isalpha():
            raise ValueError('Calls sign must start with a letter')

        callsign = value.upper()  # Normalize to uppercase
        
        base_callsign = callsign
        ssid_part = None

        if '-' in value:
            parts = value.split('-', 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Validation Failed (Callsign Format): '{callsign}' has invalid hyphen usage or empty parts.")
        
        base_callsign = parts[0]
        ssid_part = parts[1]

        # Rule: Minimum base callsign length (min 3)
        if len(base_callsign) < 3:
            raise ValueError(f"Validation Failed (Base Callsign Length): Base callsign '{base_callsign}' (from '{callsign}') must be at least 3 characters long.")

        # Rule: Base callsign characters (alphanumeric ASCII)
        # Allow A-Z, a-z, 0-9
        valid_base_chars = string.ascii_letters + string.digits
        if not all(c in valid_base_chars for c in base_callsign):
            raise ValueError(f"Validation Failed (Base Callsign Chars): Base callsign '{base_callsign}' (from '{callsign}') contains non-alphanumeric ASCII characters.")

        # --- SSID Validation (only if SSID part exists) ---
        if ssid_part is not None:
            # Rule: SSID length (1 or 2 characters)
            if not (1 <= len(ssid_part) <= 2):
                raise ValueError (f"Validation Failed (SSID Length): SSID '-{ssid_part}' (from '{callsign}') must be 1 or 2 characters long.")

            # Rule: SSID characters (alphanumeric ASCII)
            valid_ssid_chars = string.ascii_letters + string.digits
            if not all(c in valid_ssid_chars for c in ssid_part):
                raise ValueError(f"Validation Failed (SSID Chars): SSID '-{ssid_part}' (from '{callsign}') contains non-alphanumeric ASCII characters.")

            # Rule: SSID must be numeric (0-15) if transmitting
            # (can't be 0, so check if 1-15)
            if ssid_part.isdigit():
                ssid_value = int(ssid_part)
                if ssid_value < 1 or ssid_value > 15:
                    raise ValueError(f"Validation Failed (SSID Value): SSID '-{ssid_part}' (from '{callsign}') must be a numeric value between 1 and 15 if transmitting to APRS-IS.")
            else:
                raise ValueError(f"Validation Warning (SSID Non-numeric): SSID '-{ssid_part}' (from '{callsign}') is non-numeric. Numeric SSIDs are preferred and are required if transmitting to APRS-IS.")

            # Rule: SSID must not be explicitly 0
            if ssid_part == "0":
                raise ValueError(f"Validation Failed (SSID Value): Explicit SSID '-0' (from '{callsign}') is not allowed.")
    
    