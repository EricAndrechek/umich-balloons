import string
import logging
from typing import Optional, Union, Any

# Assume log setup elsewhere if needed
log = logging.getLogger(__name__)

class Callsign(str):
    """
    Pydantic custom type for callsigns based on APRS format conventions.

    Validates format: BASE-SSID where:
      - BASE is 3-6 characters, starts with a letter, alphanumeric.
      - SSID is optional, 1-2 characters, alphanumeric.
      - If SSID is numeric, it must be 1-15.
    Normalizes the callsign to uppercase.
    """
    # Max length allowed for the whole string (e.g., BASE(6) + HYPHEN(1) + SSID(2) = 9)
    max_total_length = 9
    min_base_length = 3
    max_base_length = 6
    min_ssid_length = 1
    max_ssid_length = 2

    @classmethod
    def __get_validators__(cls):
        """Yields the validation methods for Pydantic."""
        yield cls.validate_callsign_format

    @classmethod
    def validate_callsign_format(cls, value: Any) -> 'Callsign':
        """Performs the actual validation and normalization."""
        if not isinstance(value, str):
            raise TypeError('Callsign must be a string')

        callsign_upper = value.upper()  # Normalize to uppercase early

        if not callsign_upper:
             raise ValueError('Callsign cannot be empty')

        if len(callsign_upper) > cls.max_total_length:
            raise ValueError(f"Callsign '{value}' exceeds maximum length of {cls.max_total_length} characters.")

        if not callsign_upper[0].isalpha():
            raise ValueError(f"Callsign '{value}' must start with a letter.")

        base_callsign = callsign_upper
        ssid_part = None

        if '-' in callsign_upper:
            parts = callsign_upper.split('-', 1)
            # Ensure only one hyphen and non-empty parts if hyphen exists
            if len(parts) != 2 or not parts[0] or not parts[1] or '-' in parts[1]:
                 raise ValueError(f"Callsign '{value}' has invalid hyphen usage or empty parts.")
            base_callsign = parts[0]
            ssid_part = parts[1]

        # --- Base Callsign Validation ---
        # Rule: Base callsign length (min/max)
        if not (cls.min_base_length <= len(base_callsign) <= cls.max_base_length):
            raise ValueError(f"Base callsign '{base_callsign}' (from '{value}') must be {cls.min_base_length}-{cls.max_base_length} characters long.")

        # Rule: Base callsign characters (alphanumeric ASCII)
        valid_base_chars = string.ascii_uppercase + string.digits # Already uppercase
        if not all(c in valid_base_chars for c in base_callsign):
            raise ValueError(f"Base callsign '{base_callsign}' (from '{value}') contains non-alphanumeric characters.")

        # --- SSID Validation (only if SSID part exists) ---
        if ssid_part is not None:
            # Rule: SSID length
            if not (cls.min_ssid_length <= len(ssid_part) <= cls.max_ssid_length):
                raise ValueError(f"SSID '-{ssid_part}' (from '{value}') must be {cls.min_ssid_length}-{cls.max_ssid_length} characters long.")

            # Rule: SSID characters (alphanumeric ASCII)
            valid_ssid_chars = string.ascii_uppercase + string.digits # Already uppercase
            if not all(c in valid_ssid_chars for c in ssid_part):
                raise ValueError(f"SSID '-{ssid_part}' (from '{value}') contains non-alphanumeric characters.")

            # Rule: Check numeric SSID range if it's purely digits
            if ssid_part.isdigit():
                ssid_value = int(ssid_part)
                # Standard numeric SSIDs are 1-15
                if not (1 <= ssid_value <= 15):
                    raise ValueError(f"Numeric SSID '-{ssid_part}' (from '{value}') must be between 1 and 15.")
            # else:
                # Non-numeric SSIDs are allowed by this validation logic
                # log.debug(f"Non-numeric SSID '-{ssid_part}' accepted for callsign '{value}'.")
                # pass # Explicitly allow non-numeric SSIDs like -T, -PS etc.

        # If all checks pass, return the normalized, validated string
        # as an instance of the Callsign class.
        return cls(callsign_upper) # Instantiate cls with the validated string

    # Optional: Add __modify_schema__ for OpenAPI/JSON Schema generation (Pydantic V1 style)
    # or __get_pydantic_core_schema__ (Pydantic V2 style) if needed,
    # for example to add a regex pattern hint.
    # For basic validation, this is often not strictly necessary.

    # Example V2 Core Schema (optional enhancement)
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler):
         from pydantic_core import core_schema
         # Start with a basic string schema
         string_schema = core_schema.str_schema(max_length=cls.max_total_length)
         # Add our custom validation logic
         return core_schema.no_info_plain_validator_function(
             cls.validate_callsign_format,
             serialization=core_schema.to_string_ser_schema(), # How to serialize it
         )