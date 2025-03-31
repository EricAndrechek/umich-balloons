import socket
import logging
import os

logger = logging.getLogger(__name__)

# --- Defaults ---
# Default values for APRS connection
DEFAULT_APRS_HOST = "rotate.aprs.net"
DEFAULT_APRS_PORT = 14580
DEFAULT_APRS_CALLSIGN = "N0CALL"
DEFAULT_APRS_PASSCODE = "-1"  # use https://apps.magicbug.co.uk/passcode/
APP_NAME = "Umich-Balloons"  # Application name
APP_VERSION = "1.0"  # Application version

# --- Configuration (from Environment Variables) ---
APRS_HOST = os.getenv("APRS_HOST", DEFAULT_APRS_HOST)  # Use rotation service - since we are running on a server and as a public aprs client we connect to Tier 1 here
APRS_PORT = int(os.getenv("APRS_PORT", DEFAULT_APRS_PORT))

APRS_CALLSIGN = os.getenv("APRS_CALLSIGN", DEFAULT_APRS_CALLSIGN)
APRS_PASSCODE = os.getenv("APRS_PASSCODE", DEFAULT_APRS_PASSCODE)  # use https://apps.magicbug.co.uk/passcode/

# TODOs:
# - If we connect to javAPRSSrvr lets disconnect and try rotate again since it doesn't like N0CALL
# - We can query non-javAPRSSrvr for :14501/status.json
# - When we get a `# aprsc 2.1.19-g730c5c0 28 Mar 2025 22:32:27 GMT NINTH 205.233.35.46:10152` type packet
#   we can get the NINTH out (or IP) and use that to go to :14501/status.json (either ninth.aprs.net or IP)
# - Set SO_KEEPALIVE
# - Set TCP_NODELAY to turn off Nagle's algorithm
# - Set SO_REUSEADDR to allow reuse of local addresses ??
# - set a general socket timeout (and sent BEFORE connect to timeout)


# --- APRS Client Class ---
class APRSClient:
    def __init__(
        self, 
        host: str = DEFAULT_APRS_HOST, 
        port: int = DEFAULT_APRS_PORT, 
        filter_str: str = "s/O/",
        callsign: str = DEFAULT_APRS_CALLSIGN, 
        passcode: str = DEFAULT_APRS_PASSCODE,
    ):
        """
        Initialize the APRS client with the given parameters.
        All parameters are optional and will use default values if not provided.
        :param host: APRS server host
        :param port: APRS server port
        :param filter_str: APRS filter (default: "s/O/") (see https://www.aprs-is.net/javAPRSFilter.aspx)
        :param callsign: APRS callsign
        :param passcode: APRS passcode
        """

        self.host = host
        self.port = port

        self.filter_str = filter_str

        self.callsign = callsign
        self.passcode = passcode

        self.socket = None

        # Set up the socket connection

    def connect(self):
        """Connect to the APRS server with all provided parameters."""
        if self.socket:
            logger.info("Socket already connected")
            return

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(10)  # Set a timeout for the socket connection
        # Retry connection until successful or max retries reached

        max_retries = 5

        for attempt in range(max_retries):
            try:
                # Create a socket connection
                self.socket = socket.create_connection((self.host, self.port))
                logger.info(f"Connected to APRS server at {self.host}:{self.port}")
                break
            except socket.error as e:
                logger.error(f"Failed to connect to APRS server: {e}")
                logger.info("Retrying connection...")
                continue

        if not self.socket:
            logger.error("Failed to connect to APRS server after multiple attempts")
            raise ConnectionError("Unable to connect to APRS server")

        # set socket options
        # Enable keep-alive messages on the socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        # TODO: tweak these values as needed
        try:
            # Set time in seconds until first keep-alive message is sent
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            # Set time in seconds between keep-alive messages
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 60)
            # Set number of keep-alive messages to send before considering the connection dead
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        except AttributeError:
            # TCP_KEEPIDLE, TCP_KEEPINTVL, and TCP_KEEPCNT are not available on all platforms
            logger.warning("TCP_KEEPIDLE, TCP_KEEPINTVL, and TCP_KEEPCNT are not supported on this platform")
        except Exception as e:
            logger.error(f"Failed to set socket options: {e}")
            raise

        # TCP_NODELAY: Disable Nagle's algorithm for low-latency communication
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # REUSEADDR: Allow reuse of local addresses
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Send login packet to the APRS server
        login_packet = f"user {self.callsign} pass {self.passcode} vers {APP_NAME} {APP_VERSION} filter {self.filter_str}"

        try:
            self.socket.sendall(login_packet.encode())
            logger.info(f"Sent login packet: {login_packet}")
        except Exception as e:
            logger.error(f"Failed to send login packet: {e}")
            raise

    def disconnect(self):
        """Disconnect from the APRS server."""
        if self.socket:
            self.socket.close()
            logger.info("Socket closed in destructor")
        # TODO: wait for socket to close to re-use address and avoid using more than one socket
        
        self.socket = None

    def send(self, data: str):
        """Send data to the APRS server."""
        if not self.socket:
            logger.error("Socket is not connected")
            return

        # Append CR/LF sequence to the data
        data += "\r\n"

        # Assert message (including CR/LF sequence) is less than 512 bytes
        if len(data) > 512:
            logger.error("Data exceeds maximum length of 512 bytes")
            raise ValueError("Data exceeds maximum length of 512 bytes")

        try:
            self.socket.sendall(data.encode())
            logger.debug(f"Sent data: {data}")
        except Exception as e:
            logger.error(f"Failed to send data: {e}")
            raise
        # Optionally, you could implement a retry mechanism here

    def receive(self):
        """Receive data from the APRS server."""
        if not self.socket:
            logger.error("Socket is not connected")
            return None
        try:
            data = self.socket.recv(512)
            logger.debug(f"Received data: {data}")
            return data
        except Exception as e:
            logger.error(f"Failed to receive data: {e}")
            raise

    def run(self):
        """Main loop to connect, send, and receive data."""
        try:
            self.connect()
            while True:
                data = self.receive()
                if data:
                    for packet in data.splitlines():
                        if packet:
                            logger.debug(f"Received packet: {packet}")
                            # Process the received packet here
                            if packet.startswith(b"#"):
                                # Handle server messages (e.g., connection status)
                                logger.info(f"Server message: {packet.decode()}")
                            else:
                                logger.info(f"Processing APRS packet: {packet}")
                        else:
                            logger.warning("Received empty packet")
                            break
                else:
                    break
        except Exception as e:
            logger.error(f"Error in APRS client: {e}")
        finally:
            self.disconnect()
            logger.info("APRS client stopped")
            # Optionally, you could implement a retry mechanism here
            # to reconnect after a certain delay
    
    def __del__(self):
        """Destructor to ensure the socket is closed."""
        self.disconnect()
        logger.info("APRSClient instance deleted")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    aprs_client = APRSClient(
        host=APRS_HOST,
        port=APRS_PORT,
        callsign=APRS_CALLSIGN,
        passcode=APRS_PASSCODE,
    )
    aprs_client.run()