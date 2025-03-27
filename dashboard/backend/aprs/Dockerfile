# ---- Base Stage ----
FROM python:3.11-slim AS base

# Set working directory
WORKDIR /usr/src/aprs

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies (like build tools, or libpq-dev if not using -binary packages)
# RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# ---- Builder Stage ----
FROM base AS builder

# Install build dependencies
RUN pip install --upgrade pip wheel

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip wheel --no-cache-dir --wheel-dir /usr/src/aprs/wheels -r requirements.txt

# ---- Production Stage ----
FROM base AS production

# Copy installed wheels from builder stage
COPY --from=builder /usr/src/aprs/wheels /wheels

# Install runtime dependencies from wheels (faster)
RUN pip install --no-cache /wheels/* && rm -rf /wheels

# Copy application code
COPY . .

# Command to run the application
CMD ["python", "main.py"]