-- ------------------------------
-- ---------- PAYLOADS ----------
-- ------------------------------

-- One entry per payload (object being tracked)
create table if not exists public.payloads (
  id bigint generated always as identity not null,

  -- friendly name of the payload for UI
  -- db trigger auto sets this to the aprs_callsign or iridium_imei if not provided
  name text not null default ''::text,

  -- unique identifiers for the payload for various protocols, must include at least one of these
  aprs_callsign text null,
  iridium_imei text null,

  constraint payloads_pkey primary key (id),
  constraint payloads_id_key unique (id),
  constraint payloads_name_key unique (name),
  constraint payloads_aprs_callsign_key unique (aprs_callsign),
  constraint payloads_iridium_imei_key unique (iridium_imei),
  constraint payloads_at_least_one_id check (aprs_callsign is not null or iridium_imei is not null)
) TABLESPACE pg_default;

-- Create trigger function to set name if not provided
create or replace function public.set_default_payload_name()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if (NEW.name = '') then
    NEW.name := coalesce(NEW.aprs_callsign, NEW.iridium_imei);
  end if;
  return NEW;
end;
$$;

-- Create the trigger on insert
create trigger payload_default_name_trigger
before insert on public.payloads
for each row
execute function set_default_payload_name();

-- Function to merge payloads
-- This function will merge the old payload into the target payload
-- and update all references to the old payload to point to the target payload
-- so that from psql you can do:
-- psql "postgres://username:password@host:port/database?sslmode=require" -c "SELECT public.merge_payloads(<target_payload_id>, <old_payload_id>);"
CREATE OR REPLACE FUNCTION public.merge_payloads(
  target_payload_id bigint,
  old_payload_id bigint
)
RETURNS void
LANGUAGE plpgsql
security definer
set search_path = ''
AS $$
BEGIN
  -- Merge identifiers into target if missing
  UPDATE public.payloads
  SET 
    aprs_callsign = COALESCE(aprs_callsign, (SELECT aprs_callsign FROM public.payloads WHERE id = old_payload_id)),
    iridium_imei  = COALESCE(iridium_imei,  (SELECT iridium_imei FROM public.payloads WHERE id = old_payload_id))
  WHERE id = target_payload_id;

  -- Update foreign key references in raw_messages and telemetry
  UPDATE public.raw_messages
    SET payload_id = target_payload_id
    WHERE payload_id = old_payload_id;

  UPDATE public.telemetry
    SET payload_id = target_payload_id
    WHERE payload_id = old_payload_id;

  -- Delete the old payload record
  DELETE FROM public.payloads
    WHERE id = old_payload_id;

  RAISE NOTICE 'Merged payload % into payload %.', old_payload_id, target_payload_id;
END;
$$;

-- TODO: does indexing the payloads table make sense? Maybe on aprs_callsign or iridium_imei?

-- ------------------------------
-- -------- RAW_MESSAGES --------
-- ------------------------------

-- One entry per message received from a payload
-- This is the raw data received from the payload, not the parsed data
-- Can be multiple raw messages for a single payload at the same time (when data is received from multiple sources)
create table if not exists public.raw_messages (
  id bigint generated always as identity not null,
  payload_id bigint not null,

  server_received_at timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),
  -- source can be iridium, aprs-is, lora, etc
  source text not null,
  -- unique identifier for the message from the source (e.g., APRS gateway callsign, which ground station, etc) if any
  source_id text null,
  -- TODO: should this be a jsonb column? Maybe not if APRS is string?
  raw_data text not null,
  -- timestamp of the message (if included in the og message, otherwise first seen timestamp)
  data_time timestamp with time zone null,

  constraint raw_messages_pkey primary key (id),
  constraint raw_messages_id_key unique (id),
  constraint raw_messages_payload_id_fkey foreign KEY (payload_id) references payloads (id) on update CASCADE on delete CASCADE
) TABLESPACE pg_default;

-- TODO: do we need to index the raw messages by payload_id or anything else?

-- ------------------------------
-- --------- TELEMETRY  ---------
-- ------------------------------

-- One entry per telemetry message received from a payload
-- This is the parsed data from the raw message deduplicated (so the same message from multiple sources is not duplicated)
-- Unfortunately right now this parsing has to happen via the backend and not SQL, so we can't just use triggers
create table if not exists public.telemetry (
  id uuid not null default gen_random_uuid (),
  -- references payloads.id
  payload_id bigint not null,
  
  -- references a raw message by id
  -- TODO: this should be an array of foreign keys to raw_messages.id
  raw_sources bigint array not null,
  -- cleartext of sources in order received
  -- eg ['Iridium', 'APRS-IS via KD8CJT-9', 'LoRa via GS-1']
  sources text array not null,

  -- timestamp of the last time this row was updated
  last_updated timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),
  -- timestamp of the first raw message's server_received_at
  server_received_at timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),
  -- earliest known timestamp data on the payload (could be included in the raw message or based on the first seen timestamp)
  -- can be updated to be earlier (but not later) by subsequent telemetry messages
  data_time timestamp with time zone null,

  -- lat/long of the payload
  position GEOGRAPHY(POINT,4326) not null,
  -- altitude in meters
  altitude double precision null,
  -- speed in meters per second
  speed double precision null,
  -- course in degrees
  course double precision null,
  -- battery in volts
  battery double precision null,
  -- extra telemetry data
  extra jsonb null default '{}'::jsonb,
  
  constraint telemetry_pkey primary key (id),
  constraint telemetry_payload_id_fkey foreign KEY (payload_id) references payloads (id) on update CASCADE on delete CASCADE
) TABLESPACE pg_default;

-- Create a spatial index on position
create index if not exists telemetry_position_idx
  on public.telemetry
  using GIST (position);

-- Create a separate btree index for data_time (since timestamp with time zone doesn’t have a default GIST opclass)
create index if not exists telemetry_data_time_idx
  on public.telemetry
  using btree (data_time);

-- TODO: Btree gist

-- Trigger to update the last_updated timestamp on telemetry insert or update
CREATE OR REPLACE FUNCTION update_last_updated()
RETURNS TRIGGER 
LANGUAGE plpgsql
security definer
set search_path = ''
AS $$
BEGIN
  NEW.last_updated = NOW() AT TIME ZONE 'utc';
  RETURN NEW;
END;
$$;

CREATE TRIGGER last_updated_trigger
BEFORE INSERT OR UPDATE ON public.telemetry
FOR EACH ROW
EXECUTE FUNCTION update_last_updated();

-- TODO: how to make index auto update?

-- TODO: functions for querying telemetry data near a point (within x km) and for a given time range (on paths or points)
-- https://supabase.com/docs/guides/database/extensions/postgis?queryGroups=database-method&database-method=sql&queryGroups=language&language=sql

