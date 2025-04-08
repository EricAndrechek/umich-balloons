import random # For random delays
import math

import logging
import colorlog

handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s | %(name)s | %(log_color)s%(levelname)s | %(message)s"
    )
)

log = colorlog.getLogger("DELAYS")
log.addHandler(handler)
log.setLevel(logging.INFO)

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

def get_truncated_normal_delay(mu, sigma, lower, upper):
    """
    Generates a delay sampled from a normal distribution,
    truncated to the specified lower and upper bounds.
    """
    while True:
        value = random.normalvariate(mu, sigma)
        if lower <= value <= upper:
            return value
        # Keep trying until a value within bounds is generated
