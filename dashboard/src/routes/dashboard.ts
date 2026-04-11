import { Hono } from "hono";
import type {
  Env,
  LaunchGroupRow,
  PayloadRow,
  PredictionRow,
  SourceStatRow,
  UploaderStatRow,
  TelemetryCacheRow,
} from "../lib/types";

export const dashboardRoutes = new Hono<{ Bindings: Env }>();

// Full dashboard data for a launch group (single request for all panels)
dashboardRoutes.get("/:id/dashboard", async (c) => {
  const id = parseInt(c.req.param("id"));

  const group = await c.env.DB.prepare(
    "SELECT * FROM launch_groups WHERE id = ?",
  )
    .bind(id)
    .first<LaunchGroupRow>();
  if (!group) return c.json({ error: "Launch group not found" }, 404);

  // Fetch all data in parallel. Aggregate counts (source_stats, uploader_stats)
  // are derived from contacts via GROUP BY rather than maintained incrementally
  // — guarantees the numbers on the dashboard always match the raw contacts
  // table regardless of cron edge cases.
  const [payloads, predictions, sourceStats, uploaderStats, recentTelemetry, maxAltitudes, bestPerUploader] =
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

      c.env.DB.prepare(`
        SELECT
          launch_group_id,
          balloon_callsign,
          COALESCE(modulation, 'unknown') as modulation,
          COUNT(*) as packet_count
        FROM contacts
        WHERE launch_group_id = ?
        GROUP BY balloon_callsign, modulation
      `)
        .bind(id)
        .all<SourceStatRow>(),

      c.env.DB.prepare(`
        SELECT
          launch_group_id,
          balloon_callsign,
          uploader_callsign,
          COALESCE(modulation, 'unknown') as modulation,
          COUNT(*) as packet_count
        FROM contacts
        WHERE launch_group_id = ?
        GROUP BY balloon_callsign, uploader_callsign, modulation
        ORDER BY packet_count DESC
      `)
        .bind(id)
        .all<UploaderStatRow>(),

      // Last telemetry per callsign (for current values display)
      c.env.DB.prepare(`
        SELECT tc.* FROM telemetry_cache tc
        INNER JOIN (
          SELECT callsign, MAX(timestamp) as max_ts
          FROM telemetry_cache
          WHERE launch_group_id = ?
          GROUP BY callsign
        ) latest ON tc.callsign = latest.callsign AND tc.timestamp = latest.max_ts
        WHERE tc.launch_group_id = ?
      `)
        .bind(id, id)
        .all<TelemetryCacheRow>(),

      // Max altitude per callsign across the whole flight
      c.env.DB.prepare(
        "SELECT callsign, MAX(alt) as max_alt FROM telemetry_cache WHERE launch_group_id = ? GROUP BY callsign",
      )
        .bind(id)
        .all<{ callsign: string; max_alt: number | null }>(),

      // Best (farthest) contact per uploader, regardless of balloon/modulation.
      // Includes rows with NULL distance so clients can show "heard" even
      // when we couldn't compute a position (e.g. no GPS lock on uploader).
      c.env.DB.prepare(`
        SELECT uploader_callsign,
               MAX(distance_km) as best_distance_km,
               COUNT(*) as contact_count,
               MAX(contact_time) as last_contact_time
        FROM contacts
        WHERE launch_group_id = ?
        GROUP BY uploader_callsign
        ORDER BY (best_distance_km IS NULL), best_distance_km DESC
      `)
        .bind(id)
        .all<{
          uploader_callsign: string;
          best_distance_km: number | null;
          contact_count: number;
          last_contact_time: string;
        }>(),
    ]);

  const baseCallsigns = JSON.parse(group.base_callsigns) as string[];

  return c.json({
    group: {
      ...group,
      base_callsigns: baseCallsigns,
      active: !!group.active,
    },
    payloads: payloads.results,
    predictions: predictions.results,
    sourceStats: sourceStats.results,
    uploaderStats: uploaderStats.results,
    latestTelemetry: recentTelemetry.results,
    maxAltitudes: maxAltitudes.results,
    uploadersHeard: bestPerUploader.results,
  });
});

// Telemetry time series for sparklines
dashboardRoutes.get("/:id/telemetry", async (c) => {
  const id = parseInt(c.req.param("id"));
  const callsign = c.req.query("callsign");
  const limit = parseInt(c.req.query("limit") ?? "500");

  let query: string;
  let params: unknown[];

  if (callsign) {
    query = `
      SELECT * FROM telemetry_cache
      WHERE launch_group_id = ? AND callsign = ?
      ORDER BY timestamp ASC
      LIMIT ?
    `;
    params = [id, callsign, limit];
  } else {
    query = `
      SELECT * FROM telemetry_cache
      WHERE launch_group_id = ?
      ORDER BY timestamp ASC
      LIMIT ?
    `;
    params = [id, limit];
  }

  const rows = await c.env.DB.prepare(query)
    .bind(...params)
    .all<TelemetryCacheRow>();

  return c.json(rows.results);
});
