import type { SondeHubTelemetry, SondeHubListenerEntry, SondeHubPrediction } from "./types";

/** Fetch telemetry for a specific payload callsign. */
export async function fetchTelemetry(
  apiUrl: string,
  callsign: string,
  lastSeconds: number,
): Promise<SondeHubTelemetry[]> {
  const url = `${apiUrl}/amateur/telemetry/${encodeURIComponent(callsign)}?last=${lastSeconds}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    cf: { cacheTtl: 0 },
  });
  if (!res.ok) {
    console.error(`SondeHub telemetry error: ${res.status} for ${callsign}`);
    return [];
  }
  return res.json() as Promise<SondeHubTelemetry[]>;
}

/** One listener-position sample, with its reporting time as unix ms. */
export interface ListenerSample {
  timeMs: number;
  lat: number;
  lon: number;
  alt: number;
}

/**
 * Fetch all listener position history over the last day.
 * Returns a map of callsign → sorted-by-time array of samples.
 *
 * We keep the full history (not just the latest) because many ground stations
 * are mobile — chase cars, portable setups — and a balloon telemetry record's
 * distance must be computed against the uploader's position **at the time
 * that record was heard**, not against wherever the uploader happens to be
 * right now.
 */
export async function fetchListenerHistory(
  apiUrl: string,
): Promise<Map<string, ListenerSample[]>> {
  const url = `${apiUrl}/amateur/listeners/telemetry?duration=1d`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    cf: { cacheTtl: 60 }, // short TTL so mobile stations stay fresh
  });
  if (!res.ok) {
    console.error(`SondeHub listeners error: ${res.status}`);
    return new Map();
  }

  const data = (await res.json()) as Record<
    string,
    Record<string, SondeHubListenerEntry>
  >;

  const history = new Map<string, ListenerSample[]>();
  for (const [callsign, timestamps] of Object.entries(data)) {
    const samples: ListenerSample[] = [];
    for (const [ts, entry] of Object.entries(timestamps)) {
      if (!entry.uploader_position) continue;
      const timeMs = Date.parse(ts);
      if (!Number.isFinite(timeMs)) continue;
      samples.push({
        timeMs,
        lat: entry.uploader_position[0],
        lon: entry.uploader_position[1],
        alt: entry.uploader_position[2],
      });
    }
    if (samples.length === 0) continue;
    samples.sort((a, b) => a.timeMs - b.timeMs);
    history.set(callsign, samples);
  }
  return history;
}

/**
 * Find the listener sample closest in time to `targetMs`. Returns null if the
 * nearest sample is more than `maxDiffMs` away, so we don't fake a distance
 * when we have no position anywhere near the contact time.
 */
export function findClosestListener(
  samples: ListenerSample[] | undefined,
  targetMs: number,
  maxDiffMs = 3 * 60 * 1000,
): ListenerSample | null {
  if (!samples || samples.length === 0) return null;
  // Binary search for the first sample with timeMs >= targetMs.
  let lo = 0;
  let hi = samples.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (samples[mid].timeMs < targetMs) lo = mid + 1;
    else hi = mid;
  }
  // lo is the insertion point. The closest sample is either at lo or lo-1.
  let best: ListenerSample | null = null;
  let bestDiff = Infinity;
  for (const idx of [lo - 1, lo]) {
    if (idx < 0 || idx >= samples.length) continue;
    const diff = Math.abs(samples[idx].timeMs - targetMs);
    if (diff < bestDiff) {
      best = samples[idx];
      bestDiff = diff;
    }
  }
  if (!best || bestDiff > maxDiffMs) return null;
  return best;
}

/** Fetch SondeHub predictions for active amateur balloons. */
export async function fetchPredictions(
  apiUrl: string,
): Promise<SondeHubPrediction[]> {
  const url = `${apiUrl}/amateur/predictions`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    cf: { cacheTtl: 0 },
  });
  if (!res.ok) {
    console.error(`SondeHub predictions error: ${res.status}`);
    return [];
  }
  return res.json() as Promise<SondeHubPrediction[]>;
}

/**
 * Check if a callsign matches any base callsign in the list.
 * E.g., "KF8ABL-11" matches base "KF8ABL".
 */
export function matchesBaseCallsign(
  callsign: string,
  baseCallsigns: string[],
): boolean {
  const upper = callsign.toUpperCase();
  return baseCallsigns.some((base) => {
    const baseUpper = base.toUpperCase();
    return upper === baseUpper || upper.startsWith(baseUpper + "-");
  });
}
