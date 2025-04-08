import aprspy
import random
from datetime import datetime

import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("APRS")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

def build_aprs_str(callsign, lat, lon, symbol):
    """
    Build an APRS string from the given parameters.
    
    :param callsign: The callsign of the station.
    :param lat: The latitude of the station.
    :param lon: The longitude of the station.
    :param symbol: The symbol to use for the station.
    :return: An APRS string.
    """
    try:
        packet = aprspy.packets.position.PositionPacket(
            latitude=lat,
            longitude=lon,
            symbol_table=symbol[0],
            symbol_id=symbol[1],
            data_type_id="!",
            altitude=random.randint(0, 10000),
            course=random.randint(0, 360),
            speed=random.uniform(0, 100),
            comment=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        packet.source = callsign
        packet.destination = "APRS"
        packet.path = "WIDE1-1"
    
    except Exception as e:
        log.error(f"Error building APRS packet: {e}")
        return None

    # Generate the APRS string
    aprs_str = None
    try:
        aprs_str = packet.generate()
    except Exception as e:
        log.error(f"Error generating APRS string: {e}")
        return None
    
    log.debug(f"Generated APRS string: {aprs_str}")
    return aprs_str

if __name__ == "__main__":
    callsign = "CALLSIGN"
    lat = 37.7749
    lon = -122.4194
    symbol = ("/", "O")
    
    aprs_str = build_aprs_str(callsign, lat, lon, symbol)
    print(aprs_str)
