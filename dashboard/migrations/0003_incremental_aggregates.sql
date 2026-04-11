-- Incremental aggregates for the dashboard endpoint.
--
-- Before this migration every /dashboard call ran three GROUP BY queries
-- against the full `contacts` table (source_stats, uploader_stats,
-- bestPerUploader) plus a self-join against `telemetry_cache` for
-- latestTelemetry and a GROUP BY on telemetry_cache for maxAltitudes.
-- At 100 concurrent viewers across ~10 Cloudflare colos, each cache miss
-- scanned thousands of rows three to five times over, putting daily D1
-- reads well over the free-tier 5M/day limit.
--
-- After this migration the cron maintains the aggregates incrementally
-- (one UPSERT per new contact, one UPDATE per new telemetry) and the
-- dashboard endpoint reads only small pre-aggregated tables. Per-request
-- row count drops from ~30k to ~150.

-- Per-payload cached stats maintained by the cron. Authoritative across
-- the entire flight regardless of what's in the client's in-memory
-- telemetry buffer, so the dashboard total-distance and max-altitude
-- values survive page refresh, long tab lifetime, and memory pressure.
--
-- prev_track_* is the "last point the cron has already folded into
-- total_distance_km" cursor — ensures the next ingest batch picks up
-- where the previous one left off without a discontinuity.
ALTER TABLE payloads ADD COLUMN max_alt REAL;
ALTER TABLE payloads ADD COLUMN total_distance_km REAL;
ALTER TABLE payloads ADD COLUMN prev_track_lat REAL;
ALTER TABLE payloads ADD COLUMN prev_track_lon REAL;
ALTER TABLE payloads ADD COLUMN prev_track_time TEXT;

-- Backfill max_alt from existing telemetry_cache. One-time full scan, but
-- that's fine — it happens once at migration time, not per request.
UPDATE payloads SET max_alt = (
  SELECT MAX(alt) FROM telemetry_cache
  WHERE telemetry_cache.launch_group_id = payloads.launch_group_id
    AND telemetry_cache.callsign = payloads.callsign
);

-- source_stats and uploader_stats tables already exist from 0001_initial
-- but were never populated — the dashboard endpoint previously computed
-- these from raw contacts on every call. Backfill them now so an
-- already-running launch group doesn't reset to zero packet counts.
-- COALESCE guards against legacy rows with NULL modulation (0002 should
-- have cleaned these up, but belt-and-suspenders for the backfill).
INSERT OR REPLACE INTO source_stats (launch_group_id, balloon_callsign, modulation, packet_count)
  SELECT launch_group_id, balloon_callsign, COALESCE(modulation, 'unknown'), COUNT(*)
  FROM contacts
  GROUP BY launch_group_id, balloon_callsign, COALESCE(modulation, 'unknown');

INSERT OR REPLACE INTO uploader_stats (launch_group_id, balloon_callsign, uploader_callsign, modulation, packet_count)
  SELECT launch_group_id, balloon_callsign, uploader_callsign, COALESCE(modulation, 'unknown'), COUNT(*)
  FROM contacts
  GROUP BY launch_group_id, balloon_callsign, uploader_callsign, COALESCE(modulation, 'unknown');

-- "Uploaders heard" aggregate — previously computed by the dashboard
-- endpoint via GROUP BY on contacts. Now maintained incrementally. Stores
-- best-ever distance per uploader, total contact count, and most recent
-- contact time, all aggregated across balloons and modulations.
CREATE TABLE IF NOT EXISTS uploaders_heard (
  launch_group_id INTEGER NOT NULL,
  uploader_callsign TEXT NOT NULL,
  best_distance_km REAL,
  contact_count INTEGER NOT NULL DEFAULT 0,
  last_contact_time TEXT,
  PRIMARY KEY (launch_group_id, uploader_callsign)
);

-- Backfill uploaders_heard from existing contacts.
INSERT OR REPLACE INTO uploaders_heard (launch_group_id, uploader_callsign, best_distance_km, contact_count, last_contact_time)
  SELECT launch_group_id, uploader_callsign,
         MAX(distance_km),
         COUNT(*),
         MAX(contact_time)
  FROM contacts
  GROUP BY launch_group_id, uploader_callsign;

-- NOTE: total_distance_km is NOT backfilled in SQL. The sanity filters
-- (500 m/s speed cap, 10-minute session-gap break) are awkward to
-- express in pure SQL with the WINDOW + haversine combo SQLite supports,
-- and doing it badly would give a wrong authoritative number. Instead,
-- the cron self-heals on its first post-deploy tick: for any payload
-- with total_distance_km IS NULL, it reads that payload's full
-- telemetry_cache history and computes the distance using the same
-- logic the client already uses (ported into src/lib/distance.ts).
-- Until that first tick runs (max ~2 minutes), the dashboard endpoint
-- will return total_distance_km = NULL for existing payloads, and the
-- client will fall back to computing from its in-memory track as
-- before. New launches populate from zero, incrementally, from the
-- first ingest onward.
