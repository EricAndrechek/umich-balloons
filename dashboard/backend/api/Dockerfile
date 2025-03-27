# ---- Base Stage ----
FROM python:3.11-slim AS base

# Set working directory
WORKDIR /usr/src/api

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies (like build tools, or libpq-dev if not using -binary packages)
# RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
# Install curl for healthcheck
# Combine update, install, and cleanup in one RUN layer
RUN apt-get update && \
    apt-get install -y curl --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# ---- Builder Stage ----
FROM base AS builder

# Install build dependencies
RUN pip install --upgrade pip wheel

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip wheel --no-cache-dir --wheel-dir /usr/src/api/wheels -r requirements.txt

# ---- Production Stage ----
FROM base AS production

# Copy installed wheels from builder stage
COPY --from=builder /usr/src/api/wheels /wheels

# Install runtime dependencies from wheels (faster)
RUN pip install --no-cache /wheels/* && rm -rf /wheels

# Copy application code
COPY . .

# Expose the port the app runs on (matching CMD)
EXPOSE 8000

# Add user for security (optional but recommended)
# RUN adduser -S appuser
# USER appuser

# Command to run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]