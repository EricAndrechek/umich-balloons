import { Hono } from "hono";
import type { Env, ContactRow } from "../lib/types";

export const leaderboardRoutes = new Hono<{ Bindings: Env }>();

// Leaderboard: farthest contacts, sortable by balloon/modulation
leaderboardRoutes.get("/:id/leaderboard", async (c) => {
  const id = parseInt(c.req.param("id"));
  const balloonCallsign = c.req.query("balloon");
  const modulation = c.req.query("modulation");
  const limit = parseInt(c.req.query("limit") ?? "50");

  // Build the query dynamically based on filters.
  // We want each uploader's BEST contact (max distance) within the filter scope,
  // along with full detail for that record.
  const conditions: string[] = ["c.launch_group_id = ?1"];
  const innerConditions: string[] = ["launch_group_id = ?1"];
  const groupBy: string[] = ["uploader_callsign"];

  const bindings: unknown[] = [id];
  let paramIdx = 2;

  if (balloonCallsign) {
    conditions.push(`c.balloon_callsign = ?${paramIdx}`);
    innerConditions.push(`balloon_callsign = ?${paramIdx}`);
    groupBy.push("balloon_callsign");
    bindings.push(balloonCallsign);
    paramIdx++;
  }

  if (modulation) {
    conditions.push(`c.modulation = ?${paramIdx}`);
    innerConditions.push(`modulation = ?${paramIdx}`);
    groupBy.push("modulation");
    bindings.push(modulation);
    paramIdx++;
  }

  bindings.push(limit);

  const query = `
    SELECT c.* FROM contacts c
    INNER JOIN (
      SELECT uploader_callsign, ${balloonCallsign ? "balloon_callsign," : ""} ${modulation ? "modulation," : ""}
             MAX(distance_km) as max_dist
      FROM contacts
      WHERE ${innerConditions.join(" AND ")}
      GROUP BY ${groupBy.join(", ")}
    ) best ON c.uploader_callsign = best.uploader_callsign
      AND c.distance_km = best.max_dist
      ${balloonCallsign ? "AND c.balloon_callsign = best.balloon_callsign" : ""}
      ${modulation ? "AND c.modulation = best.modulation" : ""}
    WHERE ${conditions.join(" AND ")}
    ORDER BY c.distance_km DESC
    LIMIT ?${paramIdx}
  `;

  const rows = await c.env.DB.prepare(query)
    .bind(...bindings)
    .all<ContactRow>();

  return c.json(rows.results);
});

// Competition stats: who heard what, how much, by what method
leaderboardRoutes.get("/:id/competition", async (c) => {
  const id = parseInt(c.req.param("id"));

  const rows = await c.env.DB.prepare(`
    SELECT uploader_callsign, balloon_callsign, modulation,
           SUM(packet_count) as total_packets
    FROM uploader_stats
    WHERE launch_group_id = ?
    GROUP BY uploader_callsign, balloon_callsign, modulation
    ORDER BY total_packets DESC
  `)
    .bind(id)
    .all();

  return c.json(rows.results);
});
