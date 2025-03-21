# services.py
from typing import Optional
from fastapi import HTTPException
from database import supabase
from models import RawMessage, BaseTelemetryData, Telemetry

# import json  # Not needed
from datetime import datetime


def upload_raw_message(message_data: RawMessage) -> Optional[int]:
    """Uploads a raw message to the Supabase 'raw_messages' table."""
    try:
        data_to_insert = message_data.model_dump(exclude_none=True)
        if "data_time" in data_to_insert and isinstance(
            data_to_insert["data_time"], datetime
        ):
            data_to_insert["data_time"] = data_to_insert["data_time"].isoformat()
        res = supabase.table("raw_messages").insert(data_to_insert).execute()
        if res.data:
            return res.data[0]["id"]
        else:
            print(f"Error inserting raw message: {res.error}")
            return None
    except Exception as e:
        print(f"An unexpected error occurred in upload_raw_message: {e}")
        return None


def get_or_create_payload_id(
    source: str, source_identifier: str, default_name: str
) -> int:
    """Gets or creates a payload ID based on the source and identifier."""
    if source == "Iridium":
        lookup_field = "iridium_imei"
    elif source in ("MQTT", "PlainJSON"):
        lookup_field = "aprs_callsign"
    else:
        raise ValueError(f"Unsupported source type for payload lookup: {source}")

    result = (
        supabase.table("payloads")
        .select("id")
        .eq(lookup_field, source_identifier)
        .execute()
    )
    if result.data:
        return result.data[0]["id"]
    else:
        payload_insert = {lookup_field: source_identifier, "name": default_name}
        payload_result = supabase.table("payloads").insert(payload_insert).execute()
        if payload_result.data:
            return payload_result.data[0]["id"]
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create payload for {source} with identifier {source_identifier}: {payload_result.error}",
            )


async def process_telemetry_data(
    telemetry_data: BaseTelemetryData, raw_message: RawMessage
) -> None:
    """Processes telemetry data, uploads raw message, and updates/inserts telemetry."""
    try:
        if raw_message.source == "Iridium":
            payload_id = get_or_create_payload_id(
                "Iridium",
                raw_message.source_id,
                f"Iridium Device {raw_message.source_id}",
            )
        elif raw_message.source == "MQTT":
            payload_id = get_or_create_payload_id(
                "MQTT", raw_message.source_id, f"MQTT Device {raw_message.source_id}"
            )
        elif raw_message.source == "PlainJSON":
            payload_id = get_or_create_payload_id(
                "PlainJSON",
                raw_message.source_id,
                f"Plain JSON Device {raw_message.source_id}",
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported source {raw_message.source}"
            )

        raw_message.payload_id = payload_id
        raw_message_id = upload_raw_message(raw_message)
        if not raw_message_id:
            raise HTTPException(status_code=500, detail="Failed to upload raw message")

        telemetry = Telemetry.from_telemetry_data(
            payload_id=payload_id,
            raw_message_id=str(raw_message_id),
            data=telemetry_data,
        )

        # Fetch existing sources, handling the case where no record exists yet

        position_str = f"{telemetry.position.split('(')[1].split(')')[0]}"

        existing_telemetry = (
            supabase.table("telemetry")
            .select("sources")
            .eq("payload_id", payload_id)
            .eq("position", f"POINT({position_str})")
            .execute()
        )
        existing_sources = (
            existing_telemetry.data[0]["sources"] if existing_telemetry.data else []
        )

        # Merge existing and new sources

        merged_sources = list(set(existing_sources + telemetry.sources))

        # Try to update first
        update_res = (
            supabase.table("telemetry")
            .update(
                {
                    "sources": merged_sources,
                    "last_updated": telemetry.last_updated.isoformat(),
                }
            )
            .eq("payload_id", payload_id)
            .eq("position", f"POINT({position_str})")
            .execute()
        )

        if not update_res.data:
            # If update has no data, insert
            res = (
                supabase.table("telemetry")
                .insert(telemetry.to_dict(), returning="representation")
                .execute()
            )
            if not res.data:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to insert or update telemetry. Insert Error: {res.error}, Update Error: {update_res.error}",
                )

    except Exception as e:  # Catch and re-raise as HTTPException
        print(f"An unexpected error occurred in process_telemetry_data: {e}")
        if not isinstance(
            e, HTTPException
        ):  # prevent recursively raising httpexceptions
            raise HTTPException(
                status_code=500, detail=f"Internal Server Error: {str(e)}"
            )
        else:
            raise e
