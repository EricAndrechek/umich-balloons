# FastAPI Realtime Map Backend

Handles WebSocket connections and API calls for the real-time map application using FastAPI, asyncpg, Redis, and WebSockets.

## Features

* Real-time position updates via WebSockets using Redis Pub/Sub.
* Initial historical data loading based on client viewport.
* Dynamic viewport updates with catch-up data loading.
* On-demand telemetry fetching with Redis caching.
* Asynchronous database access using `asyncpg` connection pooling.
* Manual WebSocket "room" management based on a geospatial grid.
* Automatic OpenAPI documentation.
* Redis Queue for separate Celery background tasks.

## Setup

For production, it is assumed this project is used in conjunction with the other microservices in this repository. As such, you should use the Docker compose file to run the entire stack. However, if you want to run this service independently, especially for development/debugging purposes, you can do so by following these instructions.

1. **Prerequisites:**
    * Python 3.8+
    * PostgreSQL with PostGIS extension
    * Redis
    * (Optional) Celery-compatible broker (e.g., Redis) if using workers.

2. **Clone the repository:**

    ```bash
    git clone <your-repo-url>
    cd fastapi-map-backend
    ```

3. **Create a virtual environment:**

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

4. **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

5. **Configure Environment:**
    * Copy `.env.example` to `.env` (if you create an example).
    * Edit `.env` and set your `DATABASE_URL`, Redis connection details, etc.

6. **Database Setup:**
    * Ensure your PostgreSQL database and `payload_tracks` table (with PostGIS geometry and indexes) exist.

## Running the Application

### Development

For development, you can run the FastAPI application with Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production

For production, use the Docker container included in the repository.

## API Endpoints

See the OpenAPI documentation at `http://<your_host>:<your_port>/docs` for detailed API specifications.
