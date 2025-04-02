-- ------------------------------
-- ---------- PAYLOADS ----------
-- ------------------------------

-- One entry per payload (object being tracked)
create table if not exists public.payloads (
  id bigint generated always as identity not null,

  -- friendly name of the payload for UI
  -- db trigger auto sets this to the callsign if not provided
  name text not null default ''::text,

  -- unique identifiers for the payload for aprs
  callsign text null,

  constraint payloads_pkey primary key (id),
  constraint payloads_id_key unique (id),
  constraint payloads_name_key unique (name),
  constraint payloads_callsign_key unique (callsign),
  constraint payloads_at_least_one_id check (callsign is not null)
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
    NEW.name := coalesce(NEW.callsign);
  end if;
  return NEW;
end;
$$;

-- Create the trigger on insert
create trigger payload_default_name_trigger
before insert on public.payloads
for each row
execute function set_default_payload_name();

-- Create a spatial index on the payloads table
create index if not exists payloads_callsign_idx
  on public.payloads
  using btree (callsign);

-- Create a btree index for name (to sort by name)
create index if not exists payloads_name_idx
  on public.payloads
  using btree (name);

-- Create another btree for payload id (to sort by id)
create index if not exists payloads_id_idx
  on public.payloads
  using btree (id);

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

  -- data_time *should* be in data packet, but otherwise these are db-set only (cannot be set by the user):

  -- timestamp of the last time this row was updated
  last_updated timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),
  -- timestamp of the first raw message's server_received_at
  server_received_at timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),
  -- earliest known timestamp data on the payload (could be included in the raw message or based on the first seen timestamp)
  -- can be updated to be earlier (but not later) by subsequent telemetry messages
  data_time timestamp with time zone not null,

  -- actual packet data:

  -- lat/long of the payload
  position GEOGRAPHY(POINT,4326) not null,
  -- optional accuracy of the position in meters
  accuracy double precision null,
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
  constraint telemetry_payload_id_fkey foreign KEY (payload_id) references payloads (id) on update CASCADE on delete CASCADE,
  constraint telemetry_payload_time_unique UNIQUE (payload_id, data_time)
) TABLESPACE pg_default;

-- Create a spatial index on position
create index if not exists telemetry_position_idx
  on public.telemetry
  using GIST (position);

-- sortable by message timestamp
create index if not exists telemetry_data_time_idx
  on public.telemetry
  using btree (data_time);

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


-- ------------------------------
-- -------- RAW_MESSAGES --------
-- ------------------------------

-- One entry per message received from a payload
-- This is the raw data received from the payload, not the parsed data
-- Can be multiple raw messages for a single payload at the same time (when data is received from multiple sources)
create table if not exists public.raw_messages (
  id bigint generated always as identity not null,
  server_received_at timestamp with time zone not null default (now() AT TIME ZONE 'utc'::text),

  -- can be uploaded regardless of parsing:

  raw_data text not null,
  
  -- unique identifier for the message from the source (e.g., APRS gateway callsign, which ground station, etc) if any
  -- since this is done pre-parsing, it could be an IP address or anything really, just best effort from what we have
  source_id text null,

  -- updated on insert to telemetry when relevant to this raw message:
  -- references a telemetry message by id
  telemetry_id uuid null,

  -- source can be iridium, aprs-is, lora, etc
  -- add more sources as discovered (ie server name, gateway name, IP address, relay callsign, etc)
  sources text array null default '{}'::text[],

  -- TODO: should these be enums?
  -- How the server ultimately received this packet (HTTP, MQTT, APRS-IS)
  ingest_method text null,
  -- How the device originally transmitted the packet (APRS, LoRa, Iridium, etc)
  transmit_method text null,

  -- the one key sortable field that relayed the message
  relay text null,

  constraint raw_messages_pkey primary key (id),
  constraint raw_messages_id_key unique (id),

  -- invalidate/remove the telemetry_id if the telemetry message is deleted
  constraint raw_messages_telemetry_id_fkey foreign KEY (telemetry_id) references public.telemetry (id) on update CASCADE on delete SET NULL

  -- TODO: delete telemetry messages when there are no raw messages left pointing to them

) TABLESPACE pg_default;

-- sortable by when server received the message
-- leave actual time of the message to be parsed/sorted by the telemetry table
create index if not exists raw_messages_server_received_at_idx
  on public.raw_messages
  using btree (server_received_at);

-- sort by ingest method (HTTP, MQTT, APRS-IS)
create index if not exists raw_messages_ingest_method_idx
  on public.raw_messages
  using btree (ingest_method);

-- sort by transmit method (APRS, LoRa, Iridium, etc)
create index if not exists raw_messages_transmit_method_idx
  on public.raw_messages
  using btree (transmit_method);

-- sort by relay (callsign of groundstation, IP address, etc)
create index if not exists raw_messages_relay_idx
  on public.raw_messages
  using btree (relay);

-- most frequent sort is going to be by telemetry_id
create index if not exists raw_messages_telemetry_id_idx
  on public.raw_messages
  using btree (telemetry_id);

-- TODO: delete trigger or periodic cleanup job to delete telemetry 
-- messages that are not referenced by any raw messages

-- TODO: some sort of bad-data protection to prevent telemetry messages that
-- are not referenced by >1(?) raw message from being visible in the UI if
-- their location jumps over a time period (since last update) that is 
-- improbably fast (ie > 1000 km/h)

-- TODO: delete or hidden fields for all tables