# umich-balloons relay

Lightweight Go server that receives telemetry from ground stations (APRS, LoRa) and Iridium satellite webhooks, normalizes it, and forwards it to [SondeHub Amateur](https://amateur.sondehub.org).

No database, no message broker, no MQTT. Single binary.

## Architecture

```
Ground Station (APRS/LoRa) ──POST──> Go Relay ──PUT──> SondeHub Amateur API
Iridium (Rock7 webhook)    ──POST──>     │
                                         └── Batches, gzips, retries
```

## Endpoints

| Method | Path        | Description                        |
|--------|-------------|------------------------------------|
| POST   | `/aprs`     | APRS packet (JSON wrapper)         |
| POST   | `/aprs/raw` | Raw APRS packet string             |
| POST   | `/lora`     | LoRa JSON telemetry                |
| POST   | `/iridium`  | Iridium webhook (JWT-verified)     |
| GET    | `/health`   | Health check                       |

## Quick Start

```bash
cp .env.example .env
# Edit .env as needed (especially CALLSIGN_MAP and DEV_MODE)

make run
# or
go run .
```

## Docker

```bash
make docker-build
make docker-run
```

## Configuration

All configuration is via environment variables. See [.env.example](.env.example).

| Variable | Default | Description |
|----------|---------|-------------|
| `LISTEN_ADDR` | `:8080` | Server listen address |
| `SONDEHUB_API_URL` | `https://api.v2.sondehub.org` | SondeHub API base URL |
| `SOFTWARE_NAME` | `umich-balloons` | Reported to SondeHub |
| `SOFTWARE_VERSION` | `2.0.0` | Reported to SondeHub |
| `DEV_MODE` | `false` | If true, SondeHub accepts but discards data |
| `UPLOAD_INTERVAL` | `2` | Seconds between batch uploads |
| `CALLSIGN_MAP_FILE` | — | Path to JSON file mapping IMEI to callsign |
| `CALLSIGN_MAP` | — | Inline mapping: `IMEI1:CALL1,IMEI2:CALL2` |

## Request Formats

### APRS (JSON)
```json
{
  "sender": "ground-station-1",
  "raw_data": "KF8ABL-11>APRS,WIDE2-1:!4217.67N/08342.78WO/A=100000",
  "timestamp": "2024-01-15T12:00:00Z"
}
```

### LoRa
```json
{
  "sender": "ground-station-1",
  "raw_data": {
    "callsign": "KF8ABL-11",
    "lat": 42.2945,
    "lon": -83.7129,
    "alt": 30480,
    "speed": 5.2,
    "heading": 270,
    "battery": 3800,
    "sats": 12,
    "temp": -40.5
  }
}
```

### Iridium
Sent automatically by Ground Control (Rock7). JWT is verified against Rock7's public key.
