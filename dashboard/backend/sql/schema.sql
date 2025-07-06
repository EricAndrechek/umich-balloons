-- ------------------
-- --- EXTENSIONS ---
-- ------------------

-- Enable the PostGIS extension for geospatial queries
-- Edit: don't need to since using postgis docker image, already installed
-- create extension if not exists postgis with schema public;

-- CREATE EXTENSION IF NOT EXISTS postgis_geohash;

-- Maybe need for UUID?
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- GiST
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- h3
CREATE EXTENSION h3;
CREATE EXTENSION h3_postgis CASCADE;

-- ------------------------------
-- ---------- PAYLOADS ----------
-- ------------------------------

-- One entry per payload (object being tracked)
create table if not exists public.payloads (
  id uuid not null default gen_random_uuid (),

  -- friendly name of the payload for UI
  -- db trigger auto sets this to the callsign if not provided
  name text not null default ''::text,

  -- unique identifiers for the payload for aprs
  callsign text null,
  symbol text null,

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
  payload_id uuid not null,

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
  -- if null, use the default accuracy of 10m
  accuracy double precision not null default 10.0,
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
  constraint telemetry_payload_time_unique UNIQUE (payload_id, data_time),

  -- enforce no telemetry messages for the same id within 5 seconds
  CONSTRAINT telemetry_temporal_separation EXCLUDE USING GIST (payload_id WITH =, data_time WITH =)

  -- TODO: add a constraint so if position is within 1m of last position

) TABLESPACE pg_default;

-- Create a spatial index on position
create index if not exists telemetry_position_idx
  on public.telemetry
  using GIST (position);

-- create temporal index on data_time
create index if not exists telemetry_data_time_idx_gst
  on public.telemetry
  using GIST (data_time);

-- sortable by message timestamp
create index if not exists telemetry_data_time_idx_btr
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


CREATE OR REPLACE FUNCTION upsert_telemetry_complex(
    -- Input parameters matching telemetry columns
    p_payload_id uuid,
    p_data_time timestamptz,
    p_longitude double precision,
    p_latitude double precision,
    -- Optional parameters for telemetry data
    p_accuracy double precision DEFAULT 10.0,
    p_altitude double precision DEFAULT NULL,
    p_speed double precision DEFAULT NULL,
    p_course double precision DEFAULT NULL,
    p_battery double precision DEFAULT NULL,
    p_extra jsonb DEFAULT '{}'::jsonb
)
-- Returns the resulting ID and whether it was a new insert
RETURNS TABLE(result_id uuid, was_inserted boolean)
AS $$
DECLARE
    existing_record RECORD; -- To hold the full selected row if found
    v_result_id uuid;       -- To store the final ID (existing or new)
    v_was_inserted boolean; -- To store the final action status
    retry_count int := 0;
    max_retries int := 5;   -- Safety limit for retries on race conditions
    new_position GEOGRAPHY(POINT,4326); -- Pre-calculate the input position geography
BEGIN
    -- Pre-calculate new position geography object outside the loop
    new_position := ST_SetSRID(ST_MakePoint(p_longitude, p_latitude), 4326);

    -- Loop to handle potential race conditions detected during INSERT
    LOOP
        -- Step 0: Find the most recent telemetry record for the payload
        -- and check if the distance traveled in that time is reasonable
        -- (ie not a >= 500 km/h speed)
        -- if it is, just ignore this and return the last known position
        SELECT * INTO existing_record
        FROM public.telemetry
        WHERE payload_id = p_payload_id AND 
              data_time < p_data_time
        ORDER BY data_time DESC
        LIMIT 1;
        -- If no existing record, skip to INSERT
        IF FOUND THEN
            -- Check if the distance traveled is reasonable
          -- (ie not a >= 500 km/h speed)
          
          IF (ST_Distance(existing_record.position, new_position) / 1000.0) / (EXTRACT(EPOCH FROM (p_data_time - existing_record.data_time)) / 3600.0) > 500 THEN
              RAISE WARNING '[Upsert Telemetry] Distance traveled too high for payload %. Ignoring this packet.', p_payload_id;
              -- Return the last known position
              result_id := existing_record.id;
              was_inserted := false; -- Not a new insert
              RETURN NEXT; -- Add the row to the result set
              EXIT; -- Exit the loop
          END IF;
        END IF;

        -- Step 1: Find a potentially conflicting row and lock it
        -- A conflict exists if same payload AND (within 5s OR (within 1m AND both accuracies <= 10m))
        SELECT * INTO existing_record -- Select whole row
        FROM public.telemetry
        WHERE payload_id = p_payload_id
          AND (
                -- Time proximity check
                data_time BETWEEN (p_data_time - INTERVAL '5 seconds') AND (p_data_time + INTERVAL '5 seconds')
                OR
                -- Spatial proximity check (only if both accuracies are better than or equal to 10m) and is within 1m of the last packet's position and the old packet is within 1 minute
                (
                    (p_accuracy <= 10.0 AND accuracy <= 10.0)
                    AND ST_DWithin(position, new_position, 1.0)
                )
                  AND data_time >= (p_data_time - INTERVAL '1 minute')
                )
        ORDER BY
            -- Prioritize the row closest in time as the primary conflict candidate
            data_time - p_data_time ASC NULLS LAST,
            -- Secondary sort by spatial distance if applicable (for the spatial check case)
            ST_Distance(position, new_position) ASC NULLS LAST
            -- Ideally this should have only selected one row anyway...
        LIMIT 1
        FOR UPDATE; -- *** Lock the row to prevent concurrent updates/deletes ***

        -- Step 2: Handle if a conflict was found
        IF FOUND THEN
            RAISE NOTICE '[Upsert Telemetry] Conflict found for payload %, time %. Existing ID: %',
                         p_payload_id, p_data_time, existing_record.id;

            v_result_id := existing_record.id;
            v_was_inserted := false;

            -- Step 2a: determine if updating or not

            -- if new data time is >= 5 seconds from existing data time
            -- (ie we didn't really move but are updating)
            IF (p_data_time > existing_record.data_time + INTERVAL '5 seconds')
            THEN

              -- update the existing record with the new data
              RAISE NOTICE '[Upsert Telemetry] Updating existing row ID with newer data: %', existing_record.id;
              UPDATE public.telemetry
              SET
                -- don't change position if new position
                -- has a worse accuracy
                position = CASE
                  WHEN p_accuracy < accuracy
                    THEN new_position
                  ELSE position
                END,
                accuracy = CASE
                  WHEN 
                    p_accuracy < accuracy
                  THEN p_accuracy
                  ELSE accuracy
                END,
                -- update other fields if the new value is NOT NULL
                altitude = CASE WHEN p_altitude IS NOT NULL THEN p_altitude ELSE altitude END,
                speed    = CASE WHEN p_speed IS NOT NULL THEN p_speed ELSE speed END,
                course   = CASE WHEN p_course IS NOT NULL THEN p_course ELSE course END,
                battery  = CASE WHEN p_battery IS NOT NULL THEN p_battery ELSE battery END,
                extra    = CASE WHEN p_extra IS NOT NULL THEN p_extra ELSE extra END,
                -- Always update last_updated timestamp on conflict update
                last_updated = (now() AT TIME ZONE 'utc')
              WHERE id = existing_record.id;

            ELSE

              -- not a new packet, so just check if data is better
              -- if new accuracy isn't null and existing accuracy is null or worse
              IF (p_accuracy < existing_record.accuracy)

                -- if any of the other fields were null and the new value is not null
                OR (p_altitude IS NOT NULL AND existing_record.altitude IS NULL) 
                OR (p_speed IS NOT NULL AND existing_record.speed IS NULL) OR (p_course IS NOT NULL AND existing_record.course IS NULL)
                OR (p_battery IS NOT NULL AND existing_record.battery IS NULL)
                OR (p_extra IS NOT NULL AND existing_record.extra IS NULL)

              THEN
                  -- Step 2b: Perform the conditional UPDATE
                  RAISE NOTICE '[Upsert Telemetry] Updating existing row ID with better data: %', existing_record.id;
                  UPDATE public.telemetry
                  SET
                      -- Only update position/accuracy if new accuracy is better
                      position = CASE
                                  WHEN (p_accuracy < accuracy)
                                  THEN new_position
                                  ELSE position
                                END,
                      accuracy = CASE
                                  WHEN (p_accuracy < accuracy)
                                  THEN p_accuracy
                                  ELSE accuracy
                                END,
                      -- Only update other fields if the new value is NOT NULL and the OLD value IS NULL
                      altitude = CASE WHEN p_altitude IS NOT NULL AND altitude IS NULL THEN p_altitude ELSE altitude END,
                      speed    = CASE WHEN p_speed IS NOT NULL AND speed IS NULL THEN p_speed ELSE speed END,
                      course   = CASE WHEN p_course IS NOT NULL AND course IS NULL THEN p_course ELSE course END,
                      battery  = CASE WHEN p_battery IS NOT NULL AND battery IS NULL THEN p_battery ELSE battery END,
                      extra    = CASE WHEN p_extra IS NOT NULL AND extra IS NULL THEN p_extra ELSE extra END,
                      -- Always update last_updated timestamp on conflict update
                      last_updated = (now() AT TIME ZONE 'utc')
                  WHERE id = existing_record.id;
              ELSE
                  RAISE NOTICE '[Upsert Telemetry] No update needed for existing row ID: %', existing_record.id;
              END IF;

            END IF;

            -- Step 2c: Return the result (existing ID, was_inserted=false) and exit the loop/function
            result_id := v_result_id;
            was_inserted := v_was_inserted;
            RETURN NEXT; -- Add the row to the result set
            EXIT; -- Exit the LOOP

        ELSE
            -- Step 3: No conflicting row found by SELECT FOR UPDATE, attempt INSERT
            RAISE NOTICE '[Upsert Telemetry] No conflict found for payload %, time %. Attempting insert.', p_payload_id, p_data_time;
            BEGIN
                INSERT INTO public.telemetry (
                    payload_id, data_time, position, accuracy, altitude,
                    speed, course, battery, extra
                    -- server_received_at and last_updated have defaults
                ) VALUES (
                    p_payload_id, p_data_time, new_position, p_accuracy, p_altitude,
                    p_speed, p_course, p_battery, p_extra
                ) RETURNING id INTO v_result_id; -- Store the new ID

                -- If INSERT succeeds without exception
                v_was_inserted := true;
                result_id := v_result_id;
                was_inserted := v_was_inserted;
                RAISE NOTICE '[Upsert Telemetry] Insert successful. New ID: %', v_result_id;
                RETURN NEXT; -- Add the row to the result set
                EXIT; -- Exit the LOOP

            EXCEPTION
                -- Step 3a: Catch constraint violation during INSERT (likely race condition)
                WHEN unique_violation OR exclusion_violation THEN
                    retry_count := retry_count + 1;
                    RAISE WARNING '[Upsert Telemetry] Race condition detected (constraint violation) during INSERT for payload %, time %. Retry %/%',
                                p_payload_id, p_data_time, retry_count, max_retries;
                    IF retry_count >= max_retries THEN
                        RAISE EXCEPTION '[Upsert Telemetry] Upsert failed for payload %, time % after % retries due to persistent conflicts.',
                                        p_payload_id, p_data_time, max_retries;
                    END IF;
                    -- If retries not exceeded, loop will continue and retry the initial SELECT FOR UPDATE
            END; -- End INSERT exception block
        END IF; -- End IF FOUND block
    END LOOP; -- End main retry LOOP

END;
$$ LANGUAGE plpgsql VOLATILE; -- VOLATILE because it modifies data and depends on current state


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

-- =====================================================
-- === PAYLOAD PATHS - 30 MINUTE BINNED AGGREGATION  ===
-- =====================================================

-- This section adds the table and functions needed for the efficient
-- pre-aggregation of paths into 30-minute bins, intended to be combined
-- with real-time WebSocket pushes for the most current data points.

-- --------------------------------------------
-- ---------- GEOMETRY HELPER FUNCTION --------
-- --------------------------------------------
-- Calculates intersecting geohashes for a given line geometry.
-- Used by the incremental update query.

CREATE OR REPLACE FUNCTION public.generate_geohash_prefixes(full_hash TEXT)
RETURNS TEXT[]
LANGUAGE plpgsql
IMMUTABLE -- Depends only on input
STRICT    -- Returns NULL if input is NULL
AS $$
DECLARE
    prefixes TEXT[] := ARRAY[]::TEXT[];
    i INTEGER;
BEGIN
    -- No need to check for NULL here due to STRICT option
    -- No need to check length > 0, loop won't run if length is 0
    FOR i IN 1..length(full_hash) LOOP
        prefixes := array_append(prefixes, substring(full_hash from 1 for i));
    END LOOP;
    RETURN prefixes;
END;
$$;

CREATE OR REPLACE FUNCTION public.calculate_intersecting_geohashes(
    line_geom GEOMETRY,
    geohash_precision INTEGER DEFAULT 5 -- Target precision for calculation
)
RETURNS TEXT[]
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, pg_catalog
AS $$
DECLARE
    segment_length FLOAT := 100.0;
    geohash_set TEXT[] := ARRAY[]::TEXT[];
    point GEOMETRY;
    bbox GEOMETRY;
    bbox_center GEOMETRY;
    gh TEXT; -- Full precision geohash
    geom_srid INTEGER;
BEGIN
    IF line_geom IS NULL OR ST_IsEmpty(line_geom) THEN RETURN geohash_set; END IF;
    geom_srid := ST_SRID(line_geom);
    IF geom_srid IS NULL OR geom_srid = 0 THEN geom_srid := 4326; END IF;

    -- Handle Point geometry
    IF ST_GeometryType(line_geom) = 'ST_Point' THEN
        point := line_geom;
        gh := ST_GeoHash(point::geography, geohash_precision);
        -- Append prefixes using the helper function
        geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
        RETURN ARRAY(SELECT DISTINCT unnest(geohash_set) ORDER BY 1);
    END IF;

    -- Handle non-LineString/MultiLineString
    IF ST_GeometryType(line_geom) NOT LIKE 'ST_LineString' AND ST_GeometryType(line_geom) NOT LIKE 'ST_MultiLineString' THEN
        point := ST_PointOnSurface(line_geom);
        gh := ST_GeoHash(point::geography, geohash_precision);
        geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
        RETURN ARRAY(SELECT DISTINCT unnest(geohash_set) ORDER BY 1);
    END IF;

    -- Proceed for LineString/MultiLineString

    -- 1. Centroid/Point on Surface
    point := ST_PointOnSurface(line_geom);
    gh := ST_GeoHash(point::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));

    -- 2. Bounding box corners + center
    bbox := ST_Envelope(line_geom);
    bbox_center := ST_Centroid(bbox);
    gh := ST_GeoHash(bbox_center::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));

    gh := ST_GeoHash(ST_SetSRID(ST_Point(ST_XMin(bbox), ST_YMin(bbox)), geom_srid)::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
    gh := ST_GeoHash(ST_SetSRID(ST_Point(ST_XMax(bbox), ST_YMax(bbox)), geom_srid)::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
    gh := ST_GeoHash(ST_SetSRID(ST_Point(ST_XMin(bbox), ST_YMax(bbox)), geom_srid)::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
    gh := ST_GeoHash(ST_SetSRID(ST_Point(ST_XMax(bbox), ST_YMin(bbox)), geom_srid)::geography, geohash_precision);
    geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));

    -- 3. Segmentize the line
    BEGIN
        FOR point IN EXECUTE format('SELECT (ST_DumpPoints(ST_Segmentize(%L::geometry, %s))).geom', line_geom, segment_length)
        LOOP
            gh := ST_GeoHash(point::geography, geohash_precision);
            geohash_set := array_cat(geohash_set, public.generate_geohash_prefixes(gh));
        END LOOP;
    EXCEPTION WHEN OTHERS THEN
       RAISE WARNING '[calculate_intersecting_geohashes] ST_Segmentize failed for geometry %: %', ST_AsText(line_geom), SQLERRM;
    END;

    -- Return unique geohashes (including all prefixes)
    RETURN ARRAY(SELECT DISTINCT unnest(geohash_set) ORDER BY 1);
END;
$$;

-- -------------------------------------
-- ---------- BINNING TABLE ------------
-- -------------------------------------
-- Stores pre-aggregated 30-minute path segments derived from telemetry.
-- Updated frequently (e.g., every minute) via an incremental UPSERT query.
CREATE TABLE IF NOT EXISTS public.payload_paths_binned (
    payload_id              UUID NOT NULL, -- Matches public.payloads.id
    time_bin_start          TIMESTAMP WITH TIME ZONE NOT NULL, -- Start of the 30-min bin (UTC)
    path_segment_geojson    JSONB, -- GeoJSON LineString or Point
    intersecting_geohashes  TEXT[], -- Array of geohashes (e.g., precision 7)
    point_count             INTEGER NOT NULL, -- Number of points in this segment
    first_point_time        TIMESTAMP WITH TIME ZONE NOT NULL, -- Timestamp of first point in segment
    last_point_time         TIMESTAMP WITH TIME ZONE NOT NULL, -- Timestamp of last point in segment
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'utc') NOT NULL,

    -- Primary key ensures uniqueness per payload per bin, vital for ON CONFLICT
    CONSTRAINT payload_paths_binned_pkey PRIMARY KEY (payload_id, time_bin_start),

    -- Foreign key to ensure payload exists and handle cascade deletes/updates
    CONSTRAINT payload_paths_binned_payload_id_fkey FOREIGN KEY (payload_id)
        REFERENCES public.payloads (id) ON UPDATE CASCADE ON DELETE CASCADE
) TABLESPACE pg_default;

-- Index for fast time-range lookups (DESC useful for recent time queries)
CREATE INDEX IF NOT EXISTS idx_payload_paths_binned_time_bin_start
    ON public.payload_paths_binned (time_bin_start DESC);

-- Index for fast geohash lookups (Crucial for your API query using '&&' operator)
-- Requires the btree_gin extension (usually available by default)
CREATE INDEX IF NOT EXISTS idx_payload_paths_binned_geohashes_gin
    ON public.payload_paths_binned USING GIN (intersecting_geohashes);

-- Optional: Compound index for queries filtering by payload AND time
CREATE INDEX IF NOT EXISTS idx_payload_paths_binned_payload_time
    ON public.payload_paths_binned (payload_id, time_bin_start DESC);


-- -------- INCREMENTAL UPDATE QUERY --------
-- should execute frequently (e.g., every 1 minute).

CREATE OR REPLACE FUNCTION public.update_payload_paths_binned()
RETURNS INTEGER -- Return the number of rows inserted or updated
LANGUAGE plpgsql
VOLATILE -- Specifies that the function modifies the database
AS $$
DECLARE
    rows_affected INTEGER := 0; -- Variable to store the count of affected rows
BEGIN
    -- The logic previously defined for the cron job query is now inside this function:
    WITH recent_telemetry AS (
        -- Select points from the last ~32 minutes to ensure we catch points
        -- relevant to the current and potentially the immediately preceding bin.
        SELECT
            t.payload_id,
            t.data_time, -- Your timestamp column in telemetry
            t.position   -- Your GEOGRAPHY(POINT,4326) column
        FROM
            public.telemetry t -- Alias the table for clarity
        WHERE
            -- Use UTC for consistency
            t.data_time >= (now() AT TIME ZONE 'utc' - interval '32 minutes')
            AND t.data_time <= (now() AT TIME ZONE 'utc')
    ),
    binned_telemetry AS (
        -- Assign each recent point to its 30-minute time bin based on data_time
        SELECT
            rt.payload_id,
            rt.data_time,
            rt.position,
            -- Calculate the start of the 30-minute interval in UTC
            date_trunc('hour', rt.data_time AT TIME ZONE 'utc') +
                floor(extract(minute from rt.data_time AT TIME ZONE 'utc') / 30.0) * interval '30 minutes'
            AS time_bin_start -- Result is a UTC timestamp at the start of the bin
        FROM
            recent_telemetry rt
    ),
    aggregated_paths AS (
        -- Group points by payload and bin, create LineString/Point geometry, get stats
        SELECT
            bt.payload_id,
            bt.time_bin_start,
            -- Create line geometry (needs GEOMETRY input), order points by time
            -- Handle cases with only 1 point -> store as ST_Point geometry
            CASE
                WHEN count(*) > 1 THEN ST_MakeLine(bt.position::geometry ORDER BY bt.data_time ASC)
                WHEN count(*) = 1 THEN ( (array_agg(bt.position::geometry ORDER BY bt.data_time ASC))[1] )
                ELSE NULL -- Should not happen with count >= 1, but safe fallback
            END AS path_geom, -- Resulting type is GEOMETRY (LineString or Point)
            count(*) AS point_count,
            min(bt.data_time) AS first_point_time,
            max(bt.data_time) AS last_point_time
        FROM
            binned_telemetry bt
        GROUP BY
            bt.payload_id, bt.time_bin_start
        HAVING -- Ensure we only process groups with actual points
            count(*) >= 1
    )
    -- Insert new data or update existing bins based on recent telemetry
    INSERT INTO public.payload_paths_binned AS ppb ( -- Alias the target table
        payload_id,
        time_bin_start,
        path_segment_geojson,
        intersecting_geohashes,
        point_count,
        first_point_time,
        last_point_time,
        updated_at
    )
    SELECT
        ap.payload_id,
        ap.time_bin_start,
        -- Convert the generated geometry (LineString or Point) to GeoJSON
        ST_AsGeoJSON(ap.path_geom)::jsonb AS path_segment_geojson,
        -- Calculate intersecting geohashes using the helper function (Precision 7 example)
        -- Ensure the function is schema-qualified if not default search_path
        public.calculate_intersecting_geohashes(ap.path_geom, 7) AS intersecting_geohashes,
        ap.point_count,
        ap.first_point_time,
        ap.last_point_time,
        (now() AT TIME ZONE 'utc') -- Record the update time in UTC
    FROM
        aggregated_paths ap
    WHERE
        -- Ensure geometry is valid before attempting insert/update
        ap.path_geom IS NOT NULL AND NOT ST_IsEmpty(ap.path_geom)

    -- UPSERT logic: If a row for this payload/bin already exists, update it.
    ON CONFLICT (payload_id, time_bin_start)
    DO UPDATE SET
        -- Overwrite the path, geohashes, and point count with the latest calculation
        -- based *only* on the recent_telemetry window. This keeps the current bin fresh.
        path_segment_geojson = EXCLUDED.path_segment_geojson,
        intersecting_geohashes = EXCLUDED.intersecting_geohashes,
        point_count = EXCLUDED.point_count,
        -- Update time window and update timestamp
        first_point_time = LEAST(ppb.first_point_time, EXCLUDED.first_point_time), -- Keep the overall earliest start time for the bin
        last_point_time = EXCLUDED.last_point_time, -- Use the latest end time from the current calculation
        updated_at = EXCLUDED.updated_at -- Set updated_at to the time of this job run
    WHERE
        -- Optimization: Only perform the UPDATE if the new last point time is actually
        -- later than the stored last point time, preventing redundant updates if no
        -- new points arrived for a bin within the lookback window.
        EXCLUDED.last_point_time > ppb.last_point_time;

    -- Get the number of rows affected by the immediately preceding INSERT/UPDATE statement
    GET DIAGNOSTICS rows_affected = ROW_COUNT;

    -- Optional: Log notice within PostgreSQL logs
    RAISE NOTICE '[update_payload_paths_binned] Function executed. Rows inserted/updated: %', rows_affected;

    -- Return the count of affected rows
    RETURN rows_affected;

END;
$$;

-- End of Cron Job SQL Statement
