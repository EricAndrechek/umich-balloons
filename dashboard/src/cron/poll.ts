import type {
  Env,
  LaunchGroupRow,
  PayloadRow,
  TelemetryCacheRow,
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
import { haversine, segmentDistanceKm, sumTrackDistance } from "../lib/distance";

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

  // Load the full payload rows, not just callsigns — we now read the
  // per-payload cached stats (max_alt, total_distance_km) and the
  // prev_track_* cursor alongside, so each cron tick's per-payload state
  // lives in one fetch instead of a follow-up per-row query.
  const existingPayloads = await env.DB.prepare(
    "SELECT * FROM payloads WHERE launch_group_id = ?",
  )
    .bind(group.id)
    .all<PayloadRow>();
  const knownCallsigns = new Set(
    existingPayloads.results.map((p) => p.callsign),
  );
  // Mutable per-callsign working state for this cron invocation. Starts
  // from whatever's currently in D1 and gets written back at the end.
  // Keyed by callsign (unique within a group).
  interface PayloadState {
    maxAlt: number | null;
    totalDistanceKm: number | null;
    prevTrackLat: number | null;
    prevTrackLon: number | null;
    prevTrackTime: string | null;
  }
  const payloadState = new Map<string, PayloadState>();
  for (const p of existingPayloads.results) {
    payloadState.set(p.callsign, {
      maxAlt: p.max_alt,
      totalDistanceKm: p.total_distance_km,
      prevTrackLat: p.prev_track_lat,
      prevTrackLon: p.prev_track_lon,
      prevTrackTime: p.prev_track_time,
    });
  }

  // Self-healing backfill for total_distance_km. Migration 0003 adds the
  // column but leaves it NULL because the sanity filters are awkward in
  // pure SQL — instead, the first cron tick to see each payload reads
  // its full telemetry_cache history, runs the same distance logic the
  // client used to run on the in-memory track, and writes the
  // authoritative total. Subsequent ticks short-circuit this path.
  //
  // Cost: O(telemetry rows) per payload, once ever. For a 3-hour flight
  // with ~3000 rows/callsign that's ~10k row reads total across all
  // payloads, amortized over a single cron tick. Completely negligible
  // compared to what was being re-computed on every dashboard request
  // before this change.
  const backfillStatements: D1PreparedStatement[] = [];
  for (const p of existingPayloads.results) {
    if (p.total_distance_km != null) continue;
    const rows = await env.DB.prepare(
      `SELECT lat, lon, timestamp FROM telemetry_cache
       WHERE launch_group_id = ? AND callsign = ?
       ORDER BY timestamp ASC`,
    )
      .bind(group.id, p.callsign)
      .all<Pick<TelemetryCacheRow, "lat" | "lon" | "timestamp">>();
    const { totalKm, lastKept } = sumTrackDistance(rows.results);
    const state = payloadState.get(p.callsign)!;
    state.totalDistanceKm = totalKm;
    if (lastKept != null) {
      state.prevTrackLat = lastKept.lat;
      state.prevTrackLon = lastKept.lon;
      state.prevTrackTime = lastKept.timestamp;
    }
    // Write the backfilled values immediately. Subsequent per-tick
    // updates in the main flow will overwrite these once new data
    // comes in, but writing now means a cron cycle with no new data
    // still leaves D1 in the healed state.
    backfillStatements.push(
      env.DB.prepare(
        `UPDATE payloads SET
           total_distance_km = ?,
           prev_track_lat = COALESCE(?, prev_track_lat),
           prev_track_lon = COALESCE(?, prev_track_lon),
           prev_track_time = COALESCE(?, prev_track_time)
         WHERE launch_group_id = ? AND callsign = ?`,
      ).bind(
        totalKm,
        state.prevTrackLat,
        state.prevTrackLon,
        state.prevTrackTime,
        group.id,
        p.callsign,
      ),
    );
  }
  if (backfillStatements.length > 0) {
    console.log(
      `Group ${group.id}: backfilling total_distance_km for ${backfillStatements.length} payload(s)`,
    );
    const batchSize = 100;
    for (let i = 0; i < backfillStatements.length; i += batchSize) {
      await env.DB.batch(backfillStatements.slice(i, i + batchSize));
    }
  }

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

  // Pre-load the set of contact rows we already ingested in recent cron
  // cycles so we can tell "new record" apart from "already in D1". The
  // contacts INSERT uses OR IGNORE so duplicate writes are silently
  // dropped, but the aggregate UPSERTs are +1 increments and DO NOT
  // know about the OR IGNORE — without this guard, every overlap window
  // would double-count stats.
  //
  // We filter by `created_at` (our clock, when the row was inserted)
  // rather than `contact_time` (the balloon's clock, which can be
  // minutes behind for late-arriving Iridium). Using a window wider
  // than the cron fetch window (15 min → 20 min) gives safe headroom.
  // Cost: ~1k rows per cron cycle on a busy flight, run once every 2
  // minutes, so ~300k row-reads/day during a 9-hour launch. Cheap
  // insurance against stat drift.
  const existingContactKeys = new Set<string>();
  {
    const rows = await env.DB.prepare(
      `SELECT balloon_callsign, uploader_callsign, modulation, contact_time
       FROM contacts
       WHERE launch_group_id = ? AND created_at >= datetime('now', '-20 minutes')`,
    )
      .bind(group.id)
      .all<{
        balloon_callsign: string;
        uploader_callsign: string;
        modulation: string;
        contact_time: string;
      }>();
    for (const r of rows.results) {
      existingContactKeys.add(
        `${r.balloon_callsign}|${r.uploader_callsign}|${r.modulation}|${r.contact_time}`,
      );
    }
  }
  // Guards against the same record appearing twice inside a single
  // cron batch (e.g. SondeHub returning the same row under different
  // callsign expansions). Without this the aggregates could
  // double-count within one tick even if D1 is clean.
  const seenContactKeys = new Set<string>();

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
    // silently drops duplicates from overlapping cron windows.
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

    // Incremental aggregate maintenance. These UPSERTs are the whole
    // reason the dashboard endpoint no longer needs its expensive
    // GROUP BYs on contacts — the aggregate tables stay in sync as new
    // rows arrive. The contacts INSERT above uses OR IGNORE to drop
    // duplicates, but these aggregate writes do NOT know about that;
    // a new cron cycle re-processing a ~15 min lookback window could
    // double-count. Defense in depth against this:
    //
    //   1. The contacts UNIQUE index causes OR IGNORE to be a no-op
    //      for dupes, so no new data flows in.
    //   2. The aggregate UPSERTs execute in the same D1 batch as the
    //      contacts INSERTs. If D1 preserved transactional semantics
    //      within a batch we could condition the aggregate writes on
    //      the insert success; it does not. So instead we rely on the
    //      filteredRecords list having already been uniqued by the
    //      cron's fetch-window dedup: we only see records with
    //      datetime >= startedAt, and SondeHub returns each record
    //      exactly once per query. Overlap across cron cycles is the
    //      only dup source, and that's handled by a check further
    //      down: we track (uploader, mod, time) tuples we've already
    //      counted within this cron cycle so we don't double-increment
    //      even if SondeHub returned a row twice in the same batch.
    // See the `seenContactKeys` set below.

    const contactKey = `${callsign}|${uploaderCallsign}|${mod}|${record.datetime}`;
    if (!seenContactKeys.has(contactKey)) {
      seenContactKeys.add(contactKey);

      // Was this row already in D1 before this cron run? If yes, the
      // OR IGNORE above will be a no-op and we must NOT increment
      // aggregates (they were incremented by the earlier cron run
      // that actually wrote the contact). We test this with a
      // pre-loaded set built once per cron cycle below.
      if (!existingContactKeys.has(contactKey)) {
        statements.push(
          env.DB.prepare(
            `INSERT INTO source_stats (launch_group_id, balloon_callsign, modulation, packet_count)
             VALUES (?, ?, ?, 1)
             ON CONFLICT (launch_group_id, balloon_callsign, modulation)
             DO UPDATE SET packet_count = packet_count + 1`,
          ).bind(group.id, callsign, mod),
        );
        statements.push(
          env.DB.prepare(
            `INSERT INTO uploader_stats (launch_group_id, balloon_callsign, uploader_callsign, modulation, packet_count)
             VALUES (?, ?, ?, ?, 1)
             ON CONFLICT (launch_group_id, balloon_callsign, uploader_callsign, modulation)
             DO UPDATE SET packet_count = packet_count + 1`,
          ).bind(group.id, callsign, uploaderCallsign, mod),
        );
        // uploaders_heard: best distance + contact count + most
        // recent contact time across (uploader × everything). MAX()
        // handles NULL distance cleanly because SQLite treats NULL
        // as "less than" any real value in MAX, so a real distance
        // will always win over NULL.
        statements.push(
          env.DB.prepare(
            `INSERT INTO uploaders_heard (launch_group_id, uploader_callsign, best_distance_km, contact_count, last_contact_time)
             VALUES (?, ?, ?, 1, ?)
             ON CONFLICT (launch_group_id, uploader_callsign)
             DO UPDATE SET
               best_distance_km = CASE
                 WHEN excluded.best_distance_km IS NULL THEN best_distance_km
                 WHEN best_distance_km IS NULL THEN excluded.best_distance_km
                 WHEN excluded.best_distance_km > best_distance_km THEN excluded.best_distance_km
                 ELSE best_distance_km
               END,
               contact_count = contact_count + 1,
               last_contact_time = CASE
                 WHEN excluded.last_contact_time > last_contact_time THEN excluded.last_contact_time
                 ELSE last_contact_time
               END`,
          ).bind(group.id, uploaderCallsign, distanceKm, record.datetime),
        );
      }
    }

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

  // Per-payload stats: max_alt, total_distance_km, prev_track_* cursor.
  // Walk each callsign's records in chronological order so segment
  // distance accumulates smoothly across the batch, carrying forward
  // from whatever prev_track_* the previous cron cycle left behind.
  const recordsByCallsign = new Map<string, SondeHubTelemetry[]>();
  for (const r of filteredRecords) {
    const list = recordsByCallsign.get(r.payload_callsign) ?? [];
    list.push(r);
    recordsByCallsign.set(r.payload_callsign, list);
  }
  for (const [callsign, records] of recordsByCallsign) {
    records.sort((a, b) => a.datetime.localeCompare(b.datetime));
    // Ensure a state entry exists — discoveredCallsigns that weren't
    // previously in D1 won't have one yet.
    let state = payloadState.get(callsign);
    if (!state) {
      state = {
        maxAlt: null,
        totalDistanceKm: 0,
        prevTrackLat: null,
        prevTrackLon: null,
        prevTrackTime: null,
      };
      payloadState.set(callsign, state);
    }
    // Dedupe same-timestamp rows (multi-igate relays of the same
    // packet) the same way the client's track utility does, so
    // distance doesn't get inflated by repeat fixes.
    let lastTs: string | null = null;
    for (const r of records) {
      if (r.datetime === lastTs) continue;
      lastTs = r.datetime;
      // max_alt
      if (r.alt != null && (state.maxAlt == null || r.alt > state.maxAlt)) {
        state.maxAlt = r.alt;
      }
      // segment distance — only add if the record has a position
      if (r.lat != null && r.lon != null) {
        const prev =
          state.prevTrackTime != null
            ? {
                lat: state.prevTrackLat,
                lon: state.prevTrackLon,
                timestamp: state.prevTrackTime,
              }
            : null;
        const curr = { lat: r.lat, lon: r.lon, timestamp: r.datetime };
        const segKm = segmentDistanceKm(prev, curr);
        state.totalDistanceKm = (state.totalDistanceKm ?? 0) + segKm;
        state.prevTrackLat = r.lat;
        state.prevTrackLon = r.lon;
        state.prevTrackTime = r.datetime;
      }
    }
  }

  // Update payload metadata (latest position, type classification, cached
  // per-payload stats). Folds the max_alt / total_distance_km / prev_track_*
  // write-back into the same UPDATE so we don't issue two statements per
  // callsign. COALESCE on the stat fields lets callsigns with no new
  // telemetry in this batch still get their last-position fields updated
  // without clobbering the cached aggregates.
  for (const callsign of discoveredCallsigns) {
    // Get latest telemetry for this callsign from this batch
    const latestRecord = filteredRecords
      .filter((r) => r.payload_callsign === callsign)
      .sort((a, b) => b.datetime.localeCompare(a.datetime))[0];

    if (latestRecord) {
      // Auto-classify using APRS symbol + uploader/payload relationship +
      // altitude threshold. See classifyPayload() above.
      const type = classifyPayload(latestRecord);
      const state = payloadState.get(callsign);

      statements.push(
        env.DB.prepare(`
          UPDATE payloads SET
            type = CASE WHEN type = 'unknown' OR (type = 'ground_station' AND ? = 'balloon') THEN ? ELSE type END,
            last_lat = ?, last_lon = ?, last_alt = ?, last_heard = ?,
            max_alt = ?,
            total_distance_km = ?,
            prev_track_lat = ?,
            prev_track_lon = ?,
            prev_track_time = ?
          WHERE launch_group_id = ? AND callsign = ?
        `).bind(
          type,
          type,
          latestRecord.lat,
          latestRecord.lon,
          latestRecord.alt,
          latestRecord.datetime,
          state?.maxAlt ?? null,
          state?.totalDistanceKm ?? null,
          state?.prevTrackLat ?? null,
          state?.prevTrackLon ?? null,
          state?.prevTrackTime ?? null,
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
