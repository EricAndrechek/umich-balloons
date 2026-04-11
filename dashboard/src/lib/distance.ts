const R_KM = 6371;

export function haversine(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R_KM * 2 * Math.asin(Math.sqrt(a));
}

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

// ---------------------------------------------------------------------------
// Track distance sanity filters — mirrored from web/src/lib/utils/track.ts
// so server-side aggregate maintenance agrees with what the client used to
// compute from the in-memory track. Any change here should be mirrored
// there (and vice-versa) to keep the two sides numerically equivalent.
// ---------------------------------------------------------------------------

// Unphysically fast implied speed between two fixes ⇒ assume GPS glitch
// (null-island reading, stale fix, cross-session leftover) and drop the
// segment. 500 m/s ≈ 1800 km/h — faster than Concorde, well above any
// legitimate balloon or chase car.
export const TRACK_MAX_REASONABLE_MS = 500;

// Gap between two fixes larger than this breaks the segment — we can't
// claim the vehicle travelled the straight-line distance across a 10
// minute blackout, so don't count it.
export const TRACK_MAX_SEGMENT_MS = 10 * 60 * 1000;

export interface TrackPoint {
  lat: number | null;
  lon: number | null;
  /** ISO-8601 timestamp; parsed with Date.parse. */
  timestamp: string;
}

/**
 * Sum cumulative ground distance (km) across an ordered track with the
 * same sanity filters computeTrackStats uses client-side. Input must be
 * sorted oldest → newest. Duplicate-timestamp rows (multi-igate relays)
 * are collapsed to one sample before summing.
 */
export function sumTrackDistance(points: TrackPoint[]): {
  totalKm: number;
  lastKept: TrackPoint | null;
} {
  let totalKm = 0;
  let prev: { lat: number; lon: number; t: number } | null = null;
  let lastKept: TrackPoint | null = null;
  let lastTs: string | null = null;

  for (const p of points) {
    if (p.timestamp === lastTs) continue;
    lastTs = p.timestamp;
    if (p.lat == null || p.lon == null) continue;
    const t = Date.parse(p.timestamp);
    if (!Number.isFinite(t)) continue;

    if (prev != null) {
      const dtMs = t - prev.t;
      if (dtMs > 0 && dtMs <= TRACK_MAX_SEGMENT_MS) {
        const segKm = haversine(prev.lat, prev.lon, p.lat, p.lon);
        const impliedMs = (segKm * 1000) / (dtMs / 1000);
        if (impliedMs <= TRACK_MAX_REASONABLE_MS) {
          totalKm += segKm;
        }
      }
    }

    prev = { lat: p.lat, lon: p.lon, t };
    lastKept = p;
  }

  return { totalKm, lastKept };
}

/**
 * Incremental segment distance: given the cron's carry-forward cursor
 * (the last point already folded into the running total) and a newly
 * arrived point, return the km to add (0 if the segment is filtered out
 * by the sanity rules). The caller is responsible for updating the
 * cursor to the new point after the new point is written.
 */
export function segmentDistanceKm(
  prev: TrackPoint | null,
  curr: TrackPoint,
): number {
  if (prev == null) return 0;
  if (prev.lat == null || prev.lon == null) return 0;
  if (curr.lat == null || curr.lon == null) return 0;
  const prevT = Date.parse(prev.timestamp);
  const currT = Date.parse(curr.timestamp);
  if (!Number.isFinite(prevT) || !Number.isFinite(currT)) return 0;
  const dtMs = currT - prevT;
  if (dtMs <= 0 || dtMs > TRACK_MAX_SEGMENT_MS) return 0;
  const segKm = haversine(prev.lat, prev.lon, curr.lat, curr.lon);
  const impliedMs = (segKm * 1000) / (dtMs / 1000);
  if (impliedMs > TRACK_MAX_REASONABLE_MS) return 0;
  return segKm;
}
