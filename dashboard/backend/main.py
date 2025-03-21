# main.py
from typing import Dict
from fastapi import FastAPI, HTTPException, Depends, Body
import json
from models import (
    IridiumRockblockData,
    PlainJsonTelemetryData,
    MqttTelemetryData,
    IridiumTelemetryData,
    RawMessage,
)
from services import process_telemetry_data
from dependencies import verify_rockblock_jwt
import base64
from pydantic import ValidationError

app = FastAPI()


def decode_base64_data(base64_encoded_data: str) -> Dict:
    """Decodes a base64 encoded string into a dictionary."""
    base64_bytes = base64_encoded_data.encode("ascii")
    message_bytes = base64.b64decode(base64_bytes)
    message = message_bytes.decode("ascii")
    return json.loads(message)


@app.post("/webhook/iridium", dependencies=[Depends(verify_rockblock_jwt)])
async def iridium_webhook(rockblock_data: IridiumRockblockData):
    """Handles Iridium data from RockBLOCK webhooks."""
    try:
        # Extract telemetry data
        telemetry_data = IridiumTelemetryData(
            latitude=rockblock_data.iridium_latitude,
            longitude=rockblock_data.iridium_longitude,
            altitude=None,
            speed=None,
            heading=None,
            battery=None,
            data_time=rockblock_data.data_time,
            extra={"cep": rockblock_data.iridium_cep, "momsn": rockblock_data.momsn},
        )

        # Decode the 'data' field (base64 encoded)
        decoded_data = decode_base64_data(rockblock_data.data)

        telemetry_data.altitude = decoded_data.get("altitude")
        telemetry_data.speed = decoded_data.get("speed")
        telemetry_data.heading = decoded_data.get("heading")
        telemetry_data.battery = decoded_data.get("battery")
        # Create a raw message object
        raw_message = RawMessage(
            source="Iridium",
            payload_id=0,  # This will be updated in process_telemetry_data
            raw_data=rockblock_data.model_dump_json(),  # Store original POST data
            data_time=rockblock_data.data_time,
            source_id=rockblock_data.imei,
        )

        await process_telemetry_data(telemetry_data, raw_message)
        return {"message": "Iridium data processed successfully"}
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Validation Error: {e}")
    except HTTPException as e:
        raise e  # important!
    except Exception as e:
        print(f"An unexpected error occurred in the iridium webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/webhook/json")
async def plain_json_webhook(
    data: PlainJsonTelemetryData = Body(...),
):  # Use the model!
    """Handles telemetry data from a plain JSON POST request."""
    try:
        # Create a raw message object
        raw_message = RawMessage(
            source="PlainJSON",
            payload_id=0,
            raw_data=data.model_dump_json(),
            data_time=data.data_time,
            source_id=data.extra.get("callsign"),
        )
        await process_telemetry_data(data, raw_message)
        return {"message": "JSON data processed successfully"}
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Validation Error: {e}")
    except HTTPException as e:  # Catch HTTPErrors
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")


@app.post("/webhook/mqtt")
async def mqtt_webhook(data: MqttTelemetryData):
    """Handles telemetry data from MQTT."""
    try:
        # Create a raw message object
        raw_message = RawMessage(
            source="MQTT",
            payload_id=0,  # Placeholder, will be set in processing
            raw_data=data.model_dump_json(),
            data_time=data.data_time,
            source_id=data.topic,
        )
        await process_telemetry_data(data, raw_message)
        return {"message": "MQTT data processed successfully"}

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Validation Error: {e}")
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
