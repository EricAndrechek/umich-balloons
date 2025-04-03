from fastapi import HTTPException, status

from jose import jwt, JWTError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

import logging

log = logging.getLogger(__name__)

# --- Configuration ---
GROUND_CONTROL_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAlaWAVJfNWC4XfnRx96p9
cztBcdQV6l8aKmzAlZdpEcQR6MSPzlgvihaUHNJgKm8t5ShR3jcDXIOI7er30cIN
4/9aVFMe0LWZClUGgCSLc3rrMD4FzgOJ4ibD8scVyER/sirRzf5/dswJedEiMte1
ElMQy2M6IWBACry9u12kIqG0HrhaQOzc6Tr8pHUWTKft3xwGpxCkV+K1N+9HCKFc
cbwb8okRP6FFAMm5sBbw4yAu39IVvcSL43Tucaa79FzOmfGs5mMvQfvO1ua7cOLK
fAwkhxEjirC0/RYX7Wio5yL6jmykAHJqFG2HT0uyjjrQWMtoGgwv9cIcI7xbsDX6
owIDAQAB
-----END PUBLIC KEY-----"""

try:
    public_key = serialization.load_pem_public_key(
        GROUND_CONTROL_PUBLIC_KEY_PEM.encode("utf-8"), backend=default_backend()
    )
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError("Key is not an RSA public key")
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
except Exception as e:
    log.error(f"Error loading public key: {e}")
    raise SystemExit("Failed to load critical public key.") from e

ALGORITHMS = ["RS256"]  # Assuming RS256, verify if different


# --- Helper function for JWT Verification ---
def verify_groundcontrol_jwt(jwt_token: str):
    """
    Verifies the JWT signature using Ground Control's public key.
    Raises HTTPException if verification fails.
    Returns the decoded payload upon success.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate JWT signature",
    )
    try:
        # Decode and verify the JWT
        payload = jwt.decode(
            jwt_token,
            public_key_pem,
            algorithms=ALGORITHMS,
        )

    except JWTError as e:
        log.error(f"JWT Validation Error: {e}")  # Log for debugging
        raise credentials_exception from e  # Raise the HTTP exception

    return payload
