import { Hono } from "hono";
import type {
  Env,
  LaunchGroupRow,
  PayloadRow,
  PredictionRow,
  SourceStatRow,
  UploaderStatRow,
  UploadersHeardRow,
  TelemetryCacheRow,
} from "../lib/types";
import { cachedJson, HttpError } from "../lib/cache";

export const dashboardRoutes = new Hono<{ Bindings: Env }>();

// Cache TTL for public read endpoints. With Workers Paid the D1 read
// budget is effectively unlimited, so this TTL exists only as thundering-
// herd insurance — when 100 viewers' polls align, only one of them runs
// the queries and the rest serve from edge cache. 5s is short enough to
// be invisible as a freshness lag while still amortizing alignment bursts.
// The cron fans out into 3 sub-polls per scheduled invocation (see
// src/index.ts), so SondeHub data lands in D1 every ~20s and this TTL is
// well below that cadence — clients always see data within one sub-poll
// of when it was written.
const PUBLIC_CACHE_TTL = 5;

// Full dashboard data for a launch group (single request for all panels)
dashboardRoutes.get("/:id/dashboard", async (c) => {
  const id = parseInt(c.req.param("id"));

  return cachedJson(c, PUBLIC_CACHE_TTL, async () => {
    const group = await c.env.DB.prepare(
      "SELECT * FROM launch_groups WHERE id = ?",
    )
      .bind(id)
      .first<LaunchGroupRow>();
    if (!group) throw new HttpError(404, { error: "Launch group not found" });

    // All of these are now small, pre-aggregated reads. The previous
    // implementation did three GROUP BYs over the full contacts table
    // (source_stats, uploader_stats, bestPerUploader) plus a self-join
    // on telemetry_cache for latestTelemetry and a GROUP BY for
    // maxAltitudes — at peak flight each /dashboard cache miss scanned
    // tens of thousands of rows. After migration 0003 the cron
    // maintains source_stats / uploader_stats / uploaders_heard
    // incrementally, and max_alt lives directly on the payload row
    // alongside last_lat/last_lon/last_alt/last_heard, so per-request
    // row count drops to ~150. The cache still fronts this endpoint,
    // so most requests never touch D1 at all; this path now also
    // stays cheap when the cache is cold.
    const [payloads, predictions, sourceStats, uploaderStats, uploadersHeard] =
      await Promise.all([
        c.env.DB.prepare(
          "SELECT * FROM payloads WHERE launch_group_id = ?",
        )
          .bind(id)
          .all<PayloadRow>(),

        c.env.DB.prepare(
          "SELECT * FROM predictions WHERE launch_group_id = ?",
        )
          .bind(id)
          .all<PredictionRow>(),

        c.env.DB.prepare(
          "SELECT * FROM source_stats WHERE launch_group_id = ?",
        )
          .bind(id)
          .all<SourceStatRow>(),

        c.env.DB.prepare(
          `SELECT * FROM uploader_stats
           WHERE launch_group_id = ?
           ORDER BY packet_count DESC`,
        )
          .bind(id)
          .all<UploaderStatRow>(),

        // Pre-aggregated "best heard" per uploader. Matches the shape
        // of what the old GROUP BY returned, NULL-distance rows sorted
        // last so real contacts always appear first in the list.
        c.env.DB.prepare(
          `SELECT * FROM uploaders_heard
           WHERE launch_group_id = ?
           ORDER BY (best_distance_km IS NULL), best_distance_km DESC`,
        )
          .bind(id)
          .all<UploadersHeardRow>(),
      ]);

    const baseCallsigns = JSON.parse(group.base_callsigns) as string[];

    // latestTelemetry used to be a self-join on telemetry_cache; it's
    // now synthesised from payload columns (last_lat/lon/alt/heard are
    // maintained on every telemetry ingest). Clients expect
    // `TelemetryCacheRow`-shaped rows, so we fill in the missing
    // metric fields as null — none of the dashboard consumers of
    // latestTelemetry actually read temp/humidity/batt/etc. off these
    // records (they use the sparkline series from /telemetry for that),
    // so this is shape-compatible in practice.
    const latestTelemetry = payloads.results
      .filter((p) => p.last_lat != null || p.last_heard != null)
      .map((p) => ({
        id: 0,
        launch_group_id: p.launch_group_id,
        callsign: p.callsign,
        timestamp: p.last_heard ?? "",
        lat: p.last_lat,
        lon: p.last_lon,
        alt: p.last_alt,
        temp: null,
        pressure: null,
        humidity: null,
        batt: null,
        sats: null,
        vel_v: null,
        vel_h: null,
        heading: null,
        snr: null,
        rssi: null,
        modulation: null,
        uploader_callsign: null,
      }));

    // maxAltitudes: cached on the payload row itself now.
    const maxAltitudes = payloads.results.map((p) => ({
      callsign: p.callsign,
      max_alt: p.max_alt,
    }));

    return {
      group: {
        ...group,
        base_callsigns: baseCallsigns,
        active: !!group.active,
      },
      payloads: payloads.results,
      predictions: predictions.results,
      sourceStats: sourceStats.results,
      uploaderStats: uploaderStats.results,
      latestTelemetry,
      maxAltitudes,
      uploadersHeard: uploadersHeard.results,
    };
  });
});

// Telemetry time series for sparklines.
//
// Two modes:
//
//  1. Full fetch (no `since` param): returns the newest N rows
//     ordered oldest → newest. Cached at the edge for PUBLIC_CACHE_TTL
//     seconds so dashboard clients polling the same URL share the
//     response instead of each hammering D1 for ~1000 rows.
//
//  2. Delta fetch (`since=<id>`): returns every row inserted with
//     `id > since`, ordered oldest → newest, capped by a safety limit.
//     The client tracks the max `id` it has seen and uses this for
//     subsequent polls, turning a 1000-row read into a handful of
//     rows (usually 0–5). Delta responses are NOT cached — each
//     client's cursor advances independently so URLs never match.
dashboardRoutes.get("/:id/telemetry", async (c) => {
  const id = parseInt(c.req.param("id"));
  const callsign = c.req.query("callsign");
  const sinceRaw = c.req.query("since");
  const limit = parseInt(c.req.query("limit") ?? "500");

  if (sinceRaw != null) {
    // Delta path — no caching. Use `id` as the cursor because it's
    // strictly monotonic by insertion order, whereas `timestamp` can
    // repeat across multi-igate relays of the same balloon packet.
    const since = parseInt(sinceRaw);
    if (!Number.isFinite(since) || since < 0) {
      return c.json({ error: "invalid since cursor" }, 400);
    }

    let query: string;
    let params: unknown[];
    if (callsign) {
      query = `
        SELECT * FROM telemetry_cache
        WHERE launch_group_id = ? AND callsign = ? AND id > ?
        ORDER BY timestamp ASC
        LIMIT ?
      `;
      params = [id, callsign, since, limit];
    } else {
      query = `
        SELECT * FROM telemetry_cache
        WHERE launch_group_id = ? AND id > ?
        ORDER BY timestamp ASC
        LIMIT ?
      `;
      params = [id, since, limit];
    }

    const rows = await c.env.DB.prepare(query)
      .bind(...params)
      .all<TelemetryCacheRow>();
    return c.json(rows.results, 200, {
      "Cache-Control": "no-store",
      "X-Cache": "BYPASS",
    });
  }

  // Full path — cached.
  return cachedJson(c, PUBLIC_CACHE_TTL, async () => {
    let query: string;
    let params: unknown[];

    // We want the *newest* N rows, but return them oldest → newest so
    // the client can walk the series in chronological order. A subquery
    // takes the top N by timestamp DESC, then the outer SELECT flips.
    if (callsign) {
      query = `
        SELECT * FROM (
          SELECT * FROM telemetry_cache
          WHERE launch_group_id = ? AND callsign = ?
          ORDER BY timestamp DESC
          LIMIT ?
        ) ORDER BY timestamp ASC
      `;
      params = [id, callsign, limit];
    } else {
      query = `
        SELECT * FROM (
          SELECT * FROM telemetry_cache
          WHERE launch_group_id = ?
          ORDER BY timestamp DESC
          LIMIT ?
        ) ORDER BY timestamp ASC
      `;
      params = [id, limit];
    }

    const rows = await c.env.DB.prepare(query)
      .bind(...params)
      .all<TelemetryCacheRow>();
    return rows.results;
  });
});
