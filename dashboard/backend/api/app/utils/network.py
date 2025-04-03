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

    if x_forwarded_for:
        # X-Forwarded-For can be a comma-separated list, the first is usually the client
        client_ip = x_forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "Unknown"
    return client_ip
