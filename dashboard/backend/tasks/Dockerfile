# Use an appropriate Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# --- Define defaults, can be overridden in docker-compose.yml ---
ENV REDIS_URL=redis://redis:6379/0

ENV CELERY_BROKER_URL=${REDIS_URL}
ENV CELERY_RESULT_BACKEND=${REDIS_URL}

ENV LOG_LEVEL=INFO
ENV CELERY_CONCURRENCY=4
# --- End of defaults ---

# RENAME LOG_LEVEL to ENV_LOG_LEVEL
ENV ENV_LOG_LEVEL=LOG_LEVEL
# RENAME CELERY_CONCURRENCY to ENV_CELERY_CONCURRENCY
ENV ENV_CELERY_CONCURRENCY=CELERY_CONCURRENCY

# Set default timezone
ENV TZ=UTC

# Install supervisor and any system dependencies needed by your tasks
RUN apt-get update && apt-get install -y --no-install-recommends supervisor && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy the entire 'code' directory into the container's WORKDIR
# This will create /app/code/ inside the container
COPY code/ /app/code/

# Ensure the directory structure inside container is:
# /app/code/__init__.py
# /app/code/celery.py
# /app/code/watcher.py
# /app/code/jobs/...

# Expose ports if your application serves HTTP traffic (unlikely for worker)
# EXPOSE 8000

# Start Supervisor to manage watcher, worker, and beat processes
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]