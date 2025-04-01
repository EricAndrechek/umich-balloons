from fastapi import Header, Request
import logging

log = logging.getLogger(__name__)

from typing import Optional

def get_ip(request: Request, x_forwarded_for: Optional[str] = Header(None)) -> str:
    """
    Safely retrieves the client's IP address from the request.
    Handles potential errors gracefully.

    To use this function, pass the request object and the
    X-Forwarded-For header (if available) to it.

    Example:
    @app.get("/api/example", summary="Example to get IP")
    async def get_client_ip(
        request: Request,
        x_forwarded_for: str | None = Header(default=None)
    ):
        client_ip = get_ip(request, x_forwarded_for)
        return {"client_ip": client_ip}
    """

    log.debug("Retrieving client IP address...")
    client_ip = "unknown" # Initialize with default

    try:
        ip_source = x_forwarded_for or request.client.host

        if ip_source:
            # If x_forwarded_for was used, it might be a comma-separated list
            if x_forwarded_for:
                client_ip = ip_source.split(",")[0].strip()
            else:
                client_ip = ip_source # Should be request.client.host

        # Ensure we have a string, default to "unknown" if empty or None after processing
        if not client_ip:
             client_ip = "unknown"

        log.debug(f"Raw X-Forwarded-For: {x_forwarded_for}")
        log.debug(f"Raw request.client.host: {request.client.host}")
        log.debug(f"Determined client IP: {client_ip}")

    except Exception as e:
        log.error(f"Failed to get client IP address: {e}", exc_info=True)
        client_ip = "unknown"

    return client_ip