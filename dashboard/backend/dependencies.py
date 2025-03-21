# dependencies.py
from fastapi import HTTPException, Request
import jwt
from config import settings


async def verify_rockblock_jwt(request: Request):
    """Verifies the JWT in the RockBLOCK request header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=403, detail="Authorization header missing")
    try:
        scheme, token = auth_header.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=403, detail="Invalid authorization scheme")
    except ValueError:
        raise HTTPException(
            status_code=403, detail="Invalid authorization header format"
        )
    try:
        decoded_token = jwt.decode(
            token, settings.rockblock_public_key, algorithms=[settings.jwt_algorithm]
        )
        return decoded_token
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=403, detail=f"Invalid JWT: {e}")