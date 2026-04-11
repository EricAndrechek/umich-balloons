import type {
  Env,
  LaunchGroupRow,
  SondeHubTelemetry,
  SondeHubPrediction,
} from "../lib/types";
import {
  fetchTelemetry,
  fetchListenerHistory,
  findClosestListener,
  fetchPredictions,
  matchesBaseCallsign,
  type ListenerSample,
} from "../lib/sondehub";
import { haversine } from "../lib/distance";

const ALTITUDE_BALLOON_THRESHOLD = 1000; // meters — above this, classify as balloon
const LAUNCH_ALTITUDE_THRESHOLD = 500;
const LAUNCH_ALTITUDE_DELTA = 100; // must gain this much altitude to auto-detect launch

/**
 * Extract the APRS symbol code from a raw APRS packet.
 * Position reports look like `!ddmm.mmN/dddmm.mmWX...` where `/` is the
 * symbol table and `X` (the first char after the longitude hemisphere) is
 * the symbol code. Returns null if the packet isn't a recognizable position
 * report or no symbol is present.
 */
function aprsSymbol(raw: string | undefined): string | null {
  if (!raw) return null;
  // Strip the header (everything up to the first `:`), then match the
  // position indicator followed by lat/lon and capture the symbol code.
  const body = raw.split(":").slice(1).join(":");
  if (!body) return null;
  const m = body.match(/^[!=@/].*?\d{4}\.\d{2}[NS][\/\\]\d{5}\.\d{2}[EW](.)/);
  return m ? m[1] : null;
}

/**
 * Classify a payload from a single telemetry record using multiple signals.
 * Priority:
 *   1. APRS symbol `O` → balloon (highest confidence; APRS-IS sourced)
 *   2. APRS symbol for a stationary/mobile station (>, -, &, etc.) → ground_station
 *   3. Self-report (uploader == payload) → ground_station
 *   4. Altitude > 1000 m → balloon
 *   5. Default → balloon (most payloads in a launch group are balloons;
 *      admin can manually correct via the admin page if needed)
 */
function classifyPayload(record: SondeHubTelemetry): "balloon" | "ground_station" {
  const symbol = aprsSymbol(record.raw);
  if (symbol === "O") return "balloon";
  // Stationary / mobile ground-side stations. `-` house QTH, `&` gateway,
  // `>` car, `j` jeep, `k` truck, `v` van, `u` truck (semi), `R` RV.
  if (symbol && "-&>jkvuR".includes(symbol)) return "ground_station";

  if (
    record.uploader_callsign.toUpperCase() === record.payload_callsign.toUpperCase()
  ) {
    return "ground_station";
  }

  if (record.alt > ALTITUDE_BALLOON_THRESHOLD) return "balloon";

  // Being heard by another station but under the altitude threshold —
  // typical pre-launch balloon waiting on the pad. Default to balloon.
  return "balloon";
}

export async function handleCron(env: Env): Promise<void> {
  // Only process active launch groups
  const groups = await env.DB.prepare(
    "SELECT * FROM launch_groups WHERE active = 1",
  )
    .all<LaunchGroupRow>();

  if (!groups.results.length) {
    console.log("No active launch groups, skipping cron");
    return;
  }

  // Fetch listener position history once (shared across all groups). We keep
  // the full per-callsign history so mobile igates' positions can be matched
  // to the time each telemetry record was heard, not to "wherever they are now".
  const listenerHistory = await fetchListenerHistory(env.SONDEHUB_API_URL);

  // Fetch all predictions once
  const allPredictions = await fetchPredictions(env.SONDEHUB_API_URL);

  console.log(
    `Cron: ${groups.results.length} active group(s), ${listenerHistory.size} listeners, ${allPredictions.length} predictions`,
  );

  for (const group of groups.results) {
    try {
      await processLaunchGroup(env, group, listenerHistory, allPredictions);
    } catch (err) {
      console.error(`Error processing group ${group.id} (${group.name}):`, err);
    }
  }
}

// Fetch this much history per cron run (seconds). The cron fires every 2 min,
// so a 15-minute window gives us 7x overlap to catch records that arrive at
// SondeHub late — Iridium in particular can be 3+ min delayed from the
// balloon's reported datetime. Duplicates across cycles are blocked by the
// unique index on contacts, so the overlap is correctness-safe.
const CRON_FETCH_WINDOW_SECONDS = 15 * 60;

async function processLaunchGroup(
  env: Env,
  group: LaunchGroupRow,
  listenerHistory: Map<string, ListenerSample[]>,
  allPredictions: SondeHubPrediction[],
): Promise<void> {
  const baseCallsigns = JSON.parse(group.base_callsigns) as string[];
  const startedAt = group.started_at;
  if (!startedAt) return; // shouldn't happen if active, but guard

  // Get existing payloads for this group
  const existingPayloads = await env.DB.prepare(
    "SELECT callsign FROM payloads WHERE launch_group_id = ?",
  )
    .bind(group.id)
    .all<{ callsign: string }>();
  const knownCallsigns = new Set(existingPayloads.results.map((p) => p.callsign));

  // Always query the full base+SSID expansion so new payloads on different
  // SSIDs get discovered mid-flight. Previously we only re-queried known
  // callsigns after the first discovery, which meant a ground station heard
  // first would prevent a balloon on a different SSID from ever being seen.
  // Known callsigns are merged in to cover any manually inserted payloads.
  const callsignsSet = new Set<string>();
  for (const base of baseCallsigns) {
    callsignsSet.add(base);
    for (let ssid = 1; ssid <= 15; ssid++) {
      callsignsSet.add(`${base}-${ssid}`);
    }
  }
  for (const cs of knownCallsigns) {
    callsignsSet.add(cs);
  }
  const callsignsToFetch = [...callsignsSet];

  // Fetch window: cap at CRON_FETCH_WINDOW_SECONDS, but never fetch older than
  // the launch group's own start time (some groups may have just started).
  const startTime = new Date(startedAt).getTime();
  const now = Date.now();
  const sinceStartSeconds = Math.ceil((now - startTime) / 1000);
  const lookbackSeconds = Math.max(
    60,
    Math.min(sinceStartSeconds, CRON_FETCH_WINDOW_SECONDS),
  );

  // Fetch telemetry for all callsigns in parallel (batched to avoid overload)
  const allTelemetry: SondeHubTelemetry[] = [];
  const batchSize = 5;
  for (let i = 0; i < callsignsToFetch.length; i += batchSize) {
    const batch = callsignsToFetch.slice(i, i + batchSize);
    const results = await Promise.all(
      batch.map((cs) =>
        fetchTelemetry(env.SONDEHUB_API_URL, cs, lookbackSeconds),
      ),
    );
    for (const result of results) {
      allTelemetry.push(...result);
    }
  }

  // Filter to records that belong to this launch group and are after the
  // group's start time. We no longer track a per-callsign high-water mark —
  // duplicate suppression is handled by the UNIQUE index on contacts, which
  // is correct regardless of the order records arrive in from SondeHub.
  const filteredRecords: SondeHubTelemetry[] = [];
  const discoveredCallsigns = new Set<string>();

  for (const record of allTelemetry) {
    const callsign = record.payload_callsign;
    if (!matchesBaseCallsign(callsign, baseCallsigns)) continue;
    discoveredCallsigns.add(callsign);
    if (record.datetime < startedAt) continue;
    filteredRecords.push(record);
  }

  // Discover ground-station payloads from listener history. Chase vehicles
  // report their own position via /amateur/listeners/telemetry but typically
  // never appear as payload_callsign in balloon telemetry records, so the
  // discoveredCallsigns path above misses them entirely.
  const groundStationMatches: Array<{ callsign: string; latest: ListenerSample }> = [];
  for (const [callsign, samples] of listenerHistory) {
    if (!matchesBaseCallsign(callsign, baseCallsigns)) continue;
    if (samples.length === 0) continue;
    groundStationMatches.push({ callsign, latest: samples[samples.length - 1] });
  }

  if (
    filteredRecords.length === 0 &&
    discoveredCallsigns.size === 0 &&
    groundStationMatches.length === 0
  ) {
    return; // nothing to do
  }

  // Ensure all discovered callsigns have payload entries
  const statements: D1PreparedStatement[] = [];
  for (const callsign of discoveredCallsigns) {
    if (!knownCallsigns.has(callsign)) {
      statements.push(
        env.DB.prepare(
          "INSERT OR IGNORE INTO payloads (launch_group_id, callsign) VALUES (?, ?)",
        ).bind(group.id, callsign),
      );
    }
  }

  // Create/update ground-station payloads from listener history. Uses
  // INSERT OR IGNORE so we don't clobber an existing row's type (e.g. if
  // admin manually classified it, or altitude-based classification ran).
  // The UPDATE only sets type to 'ground_station' when it was previously
  // 'unknown', leaving explicit classifications alone.
  for (const { callsign, latest } of groundStationMatches) {
    const lastHeardIso = new Date(latest.timeMs).toISOString();
    statements.push(
      env.DB.prepare(
        "INSERT OR IGNORE INTO payloads (launch_group_id, callsign, type) VALUES (?, ?, 'ground_station')",
      ).bind(group.id, callsign),
    );
    statements.push(
      env.DB.prepare(`
        UPDATE payloads SET
          type = CASE WHEN type = 'unknown' THEN 'ground_station' ELSE type END,
          last_lat = ?, last_lon = ?, last_alt = ?,
          last_heard = CASE
            WHEN last_heard IS NULL OR last_heard < ? THEN ?
            ELSE last_heard
          END
        WHERE launch_group_id = ? AND callsign = ?
      `).bind(
        latest.lat,
        latest.lon,
        latest.alt,
        lastHeardIso,
        lastHeardIso,
        group.id,
        callsign,
      ),
    );
  }

  // Process each new telemetry record
  for (const record of filteredRecords) {
    const callsign = record.payload_callsign;
    const uploaderCallsign = record.uploader_callsign;

    // Compute distance if we have both positions
    let distanceKm: number | null = null;
    let uploaderLat: number | null = null;
    let uploaderLon: number | null = null;

    // First check inline uploader_position (set by a few direct uploaders).
    // Most records (our own ground station AND the SondeHub APRS-IS Gateway)
    // omit this field, so fall through to the listener history lookup.
    if (record.uploader_position) {
      uploaderLat = record.uploader_position[0];
      uploaderLon = record.uploader_position[1];
    } else {
      // Match the uploader's reported position at the time this record was
      // heard — critical for mobile stations (chase cars, portable rigs).
      const samples = listenerHistory.get(uploaderCallsign);
      const recordMs = Date.parse(record.datetime);
      const closest = Number.isFinite(recordMs)
        ? findClosestListener(samples, recordMs)
        : null;
      if (closest) {
        uploaderLat = closest.lat;
        uploaderLon = closest.lon;
      }
    }

    if (
      uploaderLat !== null &&
      uploaderLon !== null &&
      record.lat != null &&
      record.lon != null
    ) {
      distanceKm = haversine(record.lat, record.lon, uploaderLat, uploaderLon);
    }

    // Force modulation to a non-null string so the unique index treats rows
    // correctly (SQLite considers NULLs distinct in UNIQUE constraints).
    const mod = record.modulation ?? "unknown";

    // INSERT OR IGNORE: the UNIQUE index on
    // (launch_group_id, balloon_callsign, uploader_callsign, modulation, contact_time)
    // silently drops duplicates from overlapping cron windows. Aggregate
    // counts (source_stats / uploader_stats) are derived from contacts at
    // read time in dashboard.ts, so we don't maintain them here anymore.
    statements.push(
      env.DB.prepare(`
        INSERT OR IGNORE INTO contacts (
          launch_group_id, balloon_callsign, uploader_callsign, modulation,
          distance_km, balloon_lat, balloon_lon, balloon_alt,
          uploader_lat, uploader_lon, snr, rssi, frequency,
          contact_time, sondehub_time_received
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).bind(
        group.id,
        callsign,
        uploaderCallsign,
        mod,
        distanceKm,
        record.lat,
        record.lon,
        record.alt,
        uploaderLat,
        uploaderLon,
        record.snr ?? null,
        record.rssi ?? null,
        record.frequency ?? null,
        record.datetime,
        record.time_received ?? null,
      ),
    );

    // Insert telemetry cache for sparklines
    statements.push(
      env.DB.prepare(`
        INSERT INTO telemetry_cache (
          launch_group_id, callsign, timestamp,
          lat, lon, alt, temp, pressure, humidity,
          batt, sats, vel_v, vel_h, heading,
          snr, rssi, modulation, uploader_callsign
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).bind(
        group.id,
        callsign,
        record.datetime,
        record.lat,
        record.lon,
        record.alt,
        record.temp ?? null,
        record.pressure ?? null,
        record.humidity ?? null,
        record.batt ?? null,
        record.sats ?? null,
        record.vel_v ?? null,
        record.vel_h ?? null,
        record.heading ?? null,
        record.snr ?? null,
        record.rssi ?? null,
        record.modulation ?? null,
        uploaderCallsign,
      ),
    );
  }

  // Update payload metadata (latest position, type classification, launch detection)
  for (const callsign of discoveredCallsigns) {
    // Get latest telemetry for this callsign from this batch
    const latestRecord = filteredRecords
      .filter((r) => r.payload_callsign === callsign)
      .sort((a, b) => b.datetime.localeCompare(a.datetime))[0];

    if (latestRecord) {
      // Auto-classify using APRS symbol + uploader/payload relationship +
      // altitude threshold. See classifyPayload() above.
      const type = classifyPayload(latestRecord);

      statements.push(
        env.DB.prepare(`
          UPDATE payloads SET
            type = CASE WHEN type = 'unknown' OR (type = 'ground_station' AND ? = 'balloon') THEN ? ELSE type END,
            last_lat = ?, last_lon = ?, last_alt = ?, last_heard = ?
          WHERE launch_group_id = ? AND callsign = ?
        `).bind(
          type,
          type,
          latestRecord.lat,
          latestRecord.lon,
          latestRecord.alt,
          latestRecord.datetime,
          group.id,
          callsign,
        ),
      );
    }
  }

  // Auto-detect launch: check if altitude is rising significantly
  for (const callsign of discoveredCallsigns) {
    const records = filteredRecords
      .filter((r) => r.payload_callsign === callsign)
      .sort((a, b) => a.datetime.localeCompare(b.datetime));

    if (records.length >= 3) {
      const latest = records[records.length - 1];
      const earliest = records[0];

      if (
        latest.alt > LAUNCH_ALTITUDE_THRESHOLD &&
        latest.alt - earliest.alt > LAUNCH_ALTITUDE_DELTA
      ) {
        // Auto-mark as launched if not already
        statements.push(
          env.DB.prepare(`
            UPDATE payloads SET
              launched_at = COALESCE(launched_at, ?),
              phase = CASE WHEN launched_at IS NULL THEN 'ascending' ELSE phase END,
              type = 'balloon'
            WHERE launch_group_id = ? AND callsign = ? AND launched_at IS NULL
          `).bind(earliest.datetime, group.id, callsign),
        );
      }
    }
  }

  // Update predictions from SondeHub
  for (const pred of allPredictions) {
    // Check if this prediction matches any callsign in our group
    if (!matchesBaseCallsign(pred.vehicle, baseCallsigns)) continue;

    // Parse trajectory to get final predicted landing point
    let predictedLat: number | null = null;
    let predictedLon: number | null = null;
    let predictedAlt: number | null = null;
    let predictedTime: string | null = null;

    try {
      const trajectory = JSON.parse(pred.data) as Array<{
        time: number;
        lat: number;
        lon: number;
        alt: number;
      }>;
      if (trajectory.length > 0) {
        const last = trajectory[trajectory.length - 1];
        predictedLat = last.lat;
        predictedLon = last.lon;
        predictedAlt = last.alt;
        predictedTime = new Date(last.time * 1000).toISOString();
      }
    } catch {
      // ignore parse errors
    }

    // Determine phase from SondeHub prediction
    let phase: string;
    if (pred.landed) {
      phase = "landed";
    } else if (pred.descending) {
      phase = "descending";
    } else {
      phase = "ascending";
    }

    statements.push(
      env.DB.prepare(`
        INSERT INTO predictions (
          launch_group_id, balloon_callsign,
          descending, landed, ascent_rate, descent_rate, burst_altitude,
          predicted_lat, predicted_lon, predicted_alt, predicted_time,
          trajectory_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (launch_group_id, balloon_callsign)
        DO UPDATE SET
          descending = excluded.descending,
          landed = excluded.landed,
          ascent_rate = excluded.ascent_rate,
          descent_rate = excluded.descent_rate,
          burst_altitude = excluded.burst_altitude,
          predicted_lat = excluded.predicted_lat,
          predicted_lon = excluded.predicted_lon,
          predicted_alt = excluded.predicted_alt,
          predicted_time = excluded.predicted_time,
          trajectory_json = excluded.trajectory_json,
          updated_at = excluded.updated_at
      `).bind(
        group.id,
        pred.vehicle,
        pred.descending,
        pred.landed,
        pred.ascent_rate,
        pred.descent_rate,
        pred.burst_altitude,
        predictedLat,
        predictedLon,
        predictedAlt,
        predictedTime,
        pred.data,
        new Date().toISOString(),
      ),
    );

    // Update payload phase from prediction
    statements.push(
      env.DB.prepare(`
        UPDATE payloads SET
          phase = ?,
          burst_altitude = COALESCE(?, burst_altitude)
        WHERE launch_group_id = ? AND callsign = ?
      `).bind(phase, pred.burst_altitude, group.id, pred.vehicle),
    );
  }

  // Execute all statements in batches (D1 batch limit is 100)
  if (statements.length > 0) {
    console.log(
      `Group ${group.id} (${group.name}): ${allTelemetry.length} telemetry, ${filteredRecords.length} considered, ${discoveredCallsigns.size} callsigns, ${statements.length} statements`,
    );
    const batchSize = 100;
    for (let i = 0; i < statements.length; i += batchSize) {
      const batch = statements.slice(i, i + batchSize);
      await env.DB.batch(batch);
    }
  }
}
