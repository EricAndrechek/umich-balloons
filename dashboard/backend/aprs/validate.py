import logging
import string

# --- Logging Setup (Configure as needed for your application) ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Validation Function ---
def validate_aprs_login(callsign: str, passcode: str) -> bool:
    """
    Validates APRS callsign/SSID and passcode based on specified rules.

    Args:
        callsign: The callsign string to validate (e.g., "N0CALL", "N0CALL-9").
        passcode: The passcode string (e.g., "12345", "-1").

    Returns:
        True if both callsign and passcode are valid according to the rules,
        False otherwise. Logs details on validation failures.
    """
    logging.info(f"Validating login for callsign: '{callsign}', passcode: '{passcode}'")

    # --- Passcode Validation ---
    try:
        passcode_int = int(passcode)
        if passcode_int < -1:
             logging.error(f"Validation Failed (Passcode): Passcode '{passcode}' must be a non-negative integer or -1.")
             return False
        elif passcode_int == -1:
             logging.info("Passcode is -1 (Receive-Only connection).")
        # else: passcode is >= 0, which is locally valid before server check
             # logging.info("Passcode is a non-negative integer.")

    except ValueError:
        logging.error(f"Validation Failed (Passcode): Passcode '{passcode}' is not a valid integer.")
        return False

    # --- Callsign Validation ---

    # Rule 4: Total length check (max 9)
    if len(callsign) > 9:
        logging.error(f"Validation Failed (Callsign Length): '{callsign}' exceeds maximum length of 9 characters.")
        return False

    base_callsign = callsign
    ssid_part = None

    # Check for hyphen and split if present
    if '-' in callsign:
        parts = callsign.split('-', 1) # Split only on the first hyphen
        if len(parts) != 2 or not parts[1]: # Ensure exactly one hyphen and something after it
             logging.error(f"Validation Failed (Callsign Format): '{callsign}' has invalid hyphen usage.")
             return False
        base_callsign = parts[0]
        ssid_part = parts[1]

    # Rule 5: Minimum base callsign length (min 3)
    if len(base_callsign) < 3:
        logging.error(f"Validation Failed (Base Callsign Length): Base callsign '{base_callsign}' (from '{callsign}') must be at least 3 characters long.")
        return False

    # Rule 1: Base callsign characters (alphanumeric ASCII)
    if not all(c in string.ascii_letters + string.digits for c in base_callsign):
        logging.error(f"Validation Failed (Base Callsign Chars): Base callsign '{base_callsign}' (from '{callsign}') contains non-alphanumeric ASCII characters.")
        return False

    # --- SSID Validation (only if SSID part exists) ---
    if ssid_part is not None:
        # Rule 2: SSID length (1 or 2 characters)
        if not (1 <= len(ssid_part) <= 2):
            logging.error(f"Validation Failed (SSID Length): SSID '-{ssid_part}' (from '{callsign}') must be 1 or 2 characters long.")
            return False

        # Rule 1: SSID characters (alphanumeric ASCII)
        if not all(c in string.ascii_letters + string.digits for c in ssid_part):
            logging.error(f"Validation Failed (SSID Chars): SSID '-{ssid_part}' (from '{callsign}') contains non-alphanumeric ASCII characters.")
            return False

        # Rule 3: SSID must not be explicitly 0
        if ssid_part == '0':
            logging.error(f"Validation Failed (SSID Value): Explicit SSID '-0' (from '{callsign}') is not allowed.")
            return False

        # Rule 7: SSID range 0-15 is explicitly NOT checked here per notes.

    # If all checks passed
    logging.info(f"Validation Succeeded for callsign: '{callsign}', passcode: '{passcode}'")
    return True