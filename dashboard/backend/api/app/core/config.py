import os

# if not running in Docker, load environment variables from .env file
from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()

class Settings:
    HOST = os.getenv("HOST", "localhost")
    PORT = int(os.getenv("PORT", 8000))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_QUEUE_DB = int(os.getenv("REDIS_QUEUE_DB", 0))
    REDIS_CACHE_DB = int(os.getenv("REDIS_CACHE_DB", 1))

    REDIS_UPDATES_CHANNEL = os.getenv("REDIS_UPDATES_CHANNEL", "realtime-updates")

    POSTGRES_DB = os.getenv("POSTGRES_DB", "mydatabase")
    POSTGRES_USERNAME = os.getenv("POSTGRES_USERNAME", "myuser")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "mypassword")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
    
    DATABASE_URL = f"postgresql://{POSTGRES_USERNAME}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", 1))
    DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", 10))


settings = Settings()
