# High Altitude Balloon Tracking System

## Overview

This project is a real-time tracking system for high-altitude balloons, integrating various data sources ([Iridium satellites](https://rockblock.rock7.com/), APRS-IS, LoRa ground stations, etc.) into a unified platform. It uses **MQTT** for lightweight and high resiliency data syncing between hardware ground stations and a centralized server, **Supabase (PostgreSQL)** for scalable storage and real-time updates to web/app clients, a **FastAPI backend** for managing endpoints and microservice synchronization, and various scripts to sync MQTT, APRS-IS, etc. It also includes a **Mapbox**-powered frontend for visualizing balloon paths and telemetry.

## Features

- **Realtime Tracking:** Web (and maybe app...?) users see balloon positions and telemetry updates in real time, along with path tails and projected landing locations.
- **Multiple Data Sources:** Supports data from Iridium, APRS-IS, LoRa ground stations, and mobile chase vehicles.
- **Efficient Syncing:** Ground stations cache data when offline (SQLite) and sync automatically to the server via MQTT when reconnected.
- **Low Bandwidth Usage:** MQTT ensures minimal cellular data consumption for ground stations and vehicle trackers while guaranteeing reliable data transmission.
- **Scalable and Easy to Deploy:** Hosted using cloud-based services with minimal maintenance.

## Architecture

### Component Organization

#### Backend

1. **API Server (FastAPI):** Handles requests from web clients for balloon locations, paths, etc. Main public-facing endpoint tolerant of high traffic. Should cache all requests whenever possible and potentially offload tasks to background workers.
2. **Ingestion API (FastAPI):** Separate backend for handling incoming data from MQTT, Rockblock, or any other HTTP endpoints. Requires high availability and low latency, offloading all tasks to background workers.
3. **Sync Service:** Subscribes to MQTT topics when MQTT webhooks are not available. Syncs data to/from APRS-IS. Simply a background worker that runs continuously to poll these things before creating jobs for workers to handle.
4. **Database (Supabase):** Stores all position/telemetry data, balloon paths, and metadata. Provides real-time updates to web clients via WebSockets.
5. **MQTT Broker (EMQX):** Handles data syncing between hardware ground stations and the server. Must be highly available and scalable with low latency.
6. **Redis:** Queues for background workers and caching for the API server.
7. **Workers:** Background workers for handling tasks like deduplication, data validation, and syncing data to/from the database.

#### Frontend

1. **Web Client (Mapbox):** Displays balloon paths, telemetry, and projected landing locations. Uses WebSockets for real-time updates from the database.
2. **Mobile App (Future):** Provides similar functionality to the web client but optimized for mobile devices. Uses WebSockets for real-time updates.

### Data Flow

1. **Data Collection (Hardware → Server)**
   - Ground stations and chase vehicles (collecting LoRa and/or APRS messages), 3rd-party APRS gateways, and satellite relays publish data to MQTT, APRS-IS, or our REST API.
   - Data must include at least:
      - a balloon ID (callsign or IMEI)
      - a latitude/longitude (for Iridium this can be omitted and a fallback latitude/longitude calculated by Doppler shift is used instead, although this is less accurate and not recommended)
      - a timestamp (UTC) (if this is omitted, the first time the message is received by the server or ground station is used)
      - a source (e.g., "LoRa", "APRS", "Iridium", etc.) (this is often inferred from the topic or API endpoint and doesn't need to be explicitly included in the payload)
   - Optional fields include (but are not limited to):
      - speed
      - altitude
      - pressure
      - battery level
      - heading
      - any unknown keys will automatically be added to a JSONB field in the database
   - TODO: need to work out default unit types and how to handle conversions.

2. **Processing & Storage (MQTT/APRS-IS/Iridium → Backend → PostgreSQL/Supabase)**
   - a synchronization process subscribes to MQTT topics, validates messages, and ensures deduplication before inserting into the database.
   - a separate process syncs data from APRS-IS to the database, also ensuring deduplication.
   - a FastAPI backend provides endpoints for receiving data from rock7 or other HTTP sources and inserting it into the database.
   - Cleaned data is stored in Supabase (PostgreSQL).
   - Each new instance of data is copied to a separate table for historical tracking of raw data.
   - Deduplicated data is stored in a separate table for real-time tracking and visualization.
   - Each source appends a source field to the data to indicate where it came from and appends a timestamp to indicate when it was received by the server. This data is attached to any deduplicated data in the database for display in the frontend.
   - Supabase’s realtime feature broadcasts updates to web clients.

3. **Client Viewing (Web App via Supabase)**
   - A frontend built with **Mapbox** displays balloon paths and telemetry.
   - Uses Supabase’s WebSockets to receive live updates.
   - Allows users to view detailed telemetry per balloon.

4. **Two-Way Sync (Supabase → Backend → MQTT)**
   - When data is inserted or updated in Supabase, the backend pushes updates to MQTT (and optionally to APRS-IS).
   - Chase vehicles and ground stations can retrieve the latest confirmed telemetry.

## Setup Guide

### 1. Hosting & Deployment

| Component       | Service Recommendation                   | Free Tier Available? |
| --------------- | ---------------------------------------- | -------------------- |
| **MQTT Broker** | Eclipse Mosquitto (Docker, AWS, or EMQX) | ✅                    |
| **Database**    | Supabase (PostgreSQL)                    | ✅                    |
| **Backend**     | FastAPI (Fly.io, Render, or AWS Lambda)  | ✅                    |
| **Frontend**    | Vercel, Netlify, or GitHub Pages         | ✅                    |

### 2. Installing Dependencies

#### Backend (FastAPI)
```bash
pip install fastapi paho-mqtt psycopg2 asyncpg supabase

MQTT Broker (Local Testing)

docker run -d -p 1883:1883 eclipse-mosquitto
```

### 3. Configuration

Create a .env file with:

```bash
MQTT_BROKER=broker.example.com
MQTT_PORT=1883
SUPABASE_URL=https://xyzcompany.supabase.co
SUPABASE_KEY=your-anon-key
POSTGRES_URL=postgres://user:password@host:5432/dbname
```

### 4. Running the System

Start the MQTT broker (if local)

```bash
docker start mosquitto
```

Run the backend service

```bash
python main.py
```

Start the frontend (if developing locally)

```bash
npm run dev
```

## MQTT Topics & Message Format

| Topic | Direction | Payload Format |
| ----- | --------- | -------------- |
| `balloons/telemetry` | Device → Server | `{ "id": "balloon123", "lat": 40.12, "lon": -104.56, "alt": 25000, "speed": 45, "source": "LoRa" }` |
| `balloons/sync` | Server → Device | `{ "id": "balloon123", "lat": 40.12, "lon": -104.56, "alt": 25000, "confirmed": ["LoRa", "APRS"] }` |

### Key Handling Rules

- Messages without a position (lat/lon) are ignored.
- The original timestamp (time) is used if available, otherwise the server time is used.
- Duplicate messages are filtered before inserting into the database.

## Handling Offline Syncing

1. Hardware → Server Sync
   - If a device loses internet, it caches telemetry locally.
   - Upon reconnection, the device resends data, ensuring timestamps prevent duplication.
2. Server → Hardware Sync
   - When a device reconnects, it subscribes to balloons/sync.
   - The server pushes the latest known data so devices can stay updated.
3. Minimizing Data Usage
   - MQTT retains only the last known position per balloon.
   - Devices publish updates only when telemetry changes significantly.

## Contributing & Future Improvements

- [ ] Add mobile app support for chase vehicles.
- [ ] Improve message compression for even lower bandwidth usage.
- [ ] Explore using Supabase Edge Functions to automate MQTT publishing.

### Maintainers

If taking over the project:

- Ensure the .env file is configured correctly.
- Update the MQTT broker and database credentials as needed.
- Check Supabase database and triggers for any required updates.

### License

MIT License – Feel free to use and modify!

This should be **copy-paste ready** and provides a **clear** and **easy-to-follow** guide for future maintainers. 🚀
