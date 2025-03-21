import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

class Settings(BaseSettings):
    mqtt_broker: str = os.getenv("MQTT_BROKER", "broker.emqx.io")
    mqtt_port: int = int(os.getenv("MQTT_PORT", 1883))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "guest")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "guest")
    mqtt_client_id: str = os.getenv("MQTT_CLIENT_ID", "balloon-server")
    supabase_url: str = os.getenv("SUPABASE_URL", "https://yourproject.supabase.co")
    supabase_key: str = os.getenv("SUPABASE_KEY", "your-anon-key")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgres://user:password@host:5432/dbname")

    # JWT Public Key for Ground Control
    # https://docs.groundcontrol.com/iot/rockblock/web-services/receiving-mo-message#json-web-token
    groundcontrol_jwt: str = """
    -----BEGIN PUBLIC KEY-----
    MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAlaWAVJfNWC4XfnRx96p9cztBcdQV6l8aKmzAlZdpEcQR6MSPzlgvihaUHNJgKm8t5ShR3jcDXIOI7er30cIN4/9aVFMe0LWZClUGgCSLc3rrMD4FzgOJ4ibD8scVyER/sirRzf5/dswJedEiMte1ElMQy2M6IWBACry9u12kIqG0HrhaQOzc6Tr8pHUWTKft3xwGpxCkV+K1N+9HCKFccbwb8okRP6FFAMm5sBbw4yAu39IVvcSL43Tucaa79FzOmfGs5mMvQfvO1ua7cOLKfAwkhxEjirC0/RYX7Wio5yL6jmykAHJqFG2HT0uyjjrQWMtoGgwv9cIcI7xbsDX6owIDAQAB
    -----END PUBLIC KEY-----
    """
    jwt_algorithm: str = "RS256"

    rockblock_public_key: str = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwRU66+62RQPlJHbHRNNB
8xzzCuUbYewE+dv2w1fsgdQ3IZ+9EHa3lqQ9jmyn7fXat/3FU+q0hjZU2cLp7smr
mQ1wafMKLRYHwnQIVCX0grRxpA6cb3PRpx39OEAmavgmy1jJWs57/qBRuSCyRfeS
6577uUGMIhtO75teX1EQ4QFfvk2pPC43SE5nZJi0Tw7W0A3KQCEKDRrzE+N4Vkfn
dBpl0RUDsqdfczwq8zfW8MAAka+pwDXlznwsEId2AqEUwJvWGRxozhY2IXlG4M+G
ZIpt5/K+AQBH4tgqFpfuJKxTgMEkjA5o1GFmEjaNO/gfzLmKF2xpm13K1H6vEcYv
pwIDAQAB
-----END PUBLIC KEY-----"""

settings = Settings()
