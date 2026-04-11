import { Hono } from "hono";
import type {
  Env,
  LaunchGroupRow,
  PayloadRow,
  CreateLaunchGroupRequest,
  UpdateLaunchGroupRequest,
} from "../lib/types";

export const launchRoutes = new Hono<{ Bindings: Env }>();

// List all launch groups (with payloads, for the admin page)
launchRoutes.get("/", async (c) => {
  const rows = await c.env.DB.prepare(
    "SELECT * FROM launch_groups ORDER BY created_at DESC",
  )
    .all<LaunchGroupRow>();

  const groups = await Promise.all(
    rows.results.map(async (group) => {
      const payloads = await c.env.DB.prepare(
        "SELECT * FROM payloads WHERE launch_group_id = ? ORDER BY callsign",
      )
        .bind(group.id)
        .all<PayloadRow>();
      return { ...formatGroup(group), payloads: payloads.results };
    }),
  );

  return c.json(groups);
});

// List active launch groups (for homepage banner)
launchRoutes.get("/active", async (c) => {
  const rows = await c.env.DB.prepare(
    "SELECT * FROM launch_groups WHERE active = 1 ORDER BY started_at DESC",
  )
    .all<LaunchGroupRow>();

  // Include payload summaries for each active group
  const groups = await Promise.all(
    rows.results.map(async (group) => {
      const payloads = await c.env.DB.prepare(
        "SELECT * FROM payloads WHERE launch_group_id = ?",
      )
        .bind(group.id)
        .all<PayloadRow>();
      return { ...formatGroup(group), payloads: payloads.results };
    }),
  );

  return c.json(groups);
});

// Get single launch group
launchRoutes.get("/:id", async (c) => {
  const id = parseInt(c.req.param("id"));
  const row = await c.env.DB.prepare(
    "SELECT * FROM launch_groups WHERE id = ?",
  )
    .bind(id)
    .first<LaunchGroupRow>();
  if (!row) return c.json({ error: "Launch group not found" }, 404);

  const payloads = await c.env.DB.prepare(
    "SELECT * FROM payloads WHERE launch_group_id = ?",
  )
    .bind(id)
    .all<PayloadRow>();

  return c.json({ ...formatGroup(row), payloads: payloads.results });
});

// Create launch group
launchRoutes.post("/", async (c) => {
  const body = await c.req.json<CreateLaunchGroupRequest>();
  if (!body.name || !body.base_callsigns?.length) {
    return c.json({ error: "name and base_callsigns are required" }, 400);
  }

  // Deduplicate and uppercase base callsigns
  const callsigns = [...new Set(body.base_callsigns.map((s) => s.toUpperCase().trim()))];

  const result = await c.env.DB.prepare(
    "INSERT INTO launch_groups (name, base_callsigns, expected_balloon_count) VALUES (?, ?, ?)",
  )
    .bind(body.name, JSON.stringify(callsigns), body.expected_balloon_count ?? null)
    .run();

  return c.json({ id: result.meta.last_row_id }, 201);
});

// Update launch group
launchRoutes.put("/:id", async (c) => {
  const id = parseInt(c.req.param("id"));
  const body = await c.req.json<UpdateLaunchGroupRequest>();

  const existing = await c.env.DB.prepare(
    "SELECT * FROM launch_groups WHERE id = ?",
  )
    .bind(id)
    .first<LaunchGroupRow>();
  if (!existing) return c.json({ error: "Launch group not found" }, 404);

  const name = body.name ?? existing.name;
  const callsigns = body.base_callsigns
    ? [...new Set(body.base_callsigns.map((s) => s.toUpperCase().trim()))]
    : JSON.parse(existing.base_callsigns);
  const count = body.expected_balloon_count ?? existing.expected_balloon_count;

  await c.env.DB.prepare(
    "UPDATE launch_groups SET name = ?, base_callsigns = ?, expected_balloon_count = ? WHERE id = ?",
  )
    .bind(name, JSON.stringify(callsigns), count, id)
    .run();

  return c.json({ ok: true });
});

// Start tracking
launchRoutes.post("/:id/start", async (c) => {
  const id = parseInt(c.req.param("id"));
  const now = new Date().toISOString();

  await c.env.DB.prepare(
    "UPDATE launch_groups SET active = 1, started_at = ?, stopped_at = NULL WHERE id = ?",
  )
    .bind(now, id)
    .run();

  return c.json({ ok: true, started_at: now });
});

// Stop tracking
launchRoutes.post("/:id/stop", async (c) => {
  const id = parseInt(c.req.param("id"));
  const now = new Date().toISOString();

  await c.env.DB.prepare(
    "UPDATE launch_groups SET active = 0, stopped_at = ? WHERE id = ?",
  )
    .bind(now, id)
    .run();

  return c.json({ ok: true, stopped_at: now });
});

// Reset stats for a launch group
launchRoutes.post("/:id/reset", async (c) => {
  const id = parseInt(c.req.param("id"));

  await c.env.DB.batch([
    c.env.DB.prepare("DELETE FROM contacts WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM uploader_stats WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM source_stats WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM uploaders_heard WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM telemetry_cache WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM predictions WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM cron_state WHERE launch_group_id = ?").bind(id),
    // Wipe cached per-payload stats alongside the live-position fields.
    // Leaving these non-null after a reset would make the dashboard
    // show stale max_alt / total_distance_km from the previous run.
    c.env.DB.prepare(
      `UPDATE payloads SET
         phase = 'pre-launch',
         launched_at = NULL,
         burst_altitude = NULL,
         recovered = 0,
         last_lat = NULL, last_lon = NULL, last_alt = NULL, last_heard = NULL,
         max_alt = NULL,
         total_distance_km = NULL,
         prev_track_lat = NULL,
         prev_track_lon = NULL,
         prev_track_time = NULL
       WHERE launch_group_id = ?`,
    ).bind(id),
  ]);

  return c.json({ ok: true });
});

// Manually mark a payload as launched
launchRoutes.post("/:id/payloads/:callsign/launched", async (c) => {
  const id = parseInt(c.req.param("id"));
  const callsign = decodeURIComponent(c.req.param("callsign"));
  const now = new Date().toISOString();

  await c.env.DB.prepare(
    "UPDATE payloads SET launched_at = ?, phase = 'ascending', type = 'balloon' WHERE launch_group_id = ? AND callsign = ?",
  )
    .bind(now, id, callsign)
    .run();

  return c.json({ ok: true, launched_at: now });
});

// Toggle recovered status
launchRoutes.post("/:id/payloads/:callsign/recovered", async (c) => {
  const id = parseInt(c.req.param("id"));
  const callsign = decodeURIComponent(c.req.param("callsign"));

  await c.env.DB.prepare(
    "UPDATE payloads SET recovered = CASE WHEN recovered = 0 THEN 1 ELSE 0 END WHERE launch_group_id = ? AND callsign = ?",
  )
    .bind(id, callsign)
    .run();

  return c.json({ ok: true });
});

// Delete launch group
launchRoutes.delete("/:id", async (c) => {
  const id = parseInt(c.req.param("id"));

  // CASCADE should handle child tables, but be explicit
  await c.env.DB.batch([
    c.env.DB.prepare("DELETE FROM contacts WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM uploader_stats WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM source_stats WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM uploaders_heard WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM telemetry_cache WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM predictions WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM cron_state WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM payloads WHERE launch_group_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM launch_groups WHERE id = ?").bind(id),
  ]);

  return c.json({ ok: true });
});

function formatGroup(row: LaunchGroupRow) {
  return {
    ...row,
    base_callsigns: JSON.parse(row.base_callsigns) as string[],
    active: !!row.active,
  };
}
