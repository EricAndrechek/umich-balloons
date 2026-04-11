import type { TelemetryCache } from '$lib/types';
import { haversine } from './haversine';

export interface TrackStats {
	/**
	 * Cumulative ground distance (km) across the series, with sanity
	 * filters applied: segments with an unphysical implied speed (e.g. a
	 * GPS glitch teleporting across the state) or an excessive time gap
	 * (e.g. a previous session's data in the same launch group) are
	 * dropped instead of being naively summed in.
	 */
	totalKm: number | null;
	/** Most recent rolling vertical rate (m/s, positive = ascending). */
	verticalMS: number | null;
	/** Most recent rolling ground speed (m/s). */
	horizontalMS: number | null;
	/** Peak sustained climb rate observed in the track. */
	maxAscentMS: number | null;
	/** Peak sustained descent rate (positive magnitude). */
	maxDescentMS: number | null;
	/** Peak sustained ground speed. */
	maxGroundMS: number | null;
	/** Per-point rolling vertical rate — `null` where undefined. */
	verticalSeries: Array<number | null>;
	/** Per-point rolling ground speed — `null` where undefined. */
	horizontalSeries: Array<number | null>;
}

// Any segment implying more than this is assumed to be corrupt data (a
// stale GPS fix, a null-island reading, or a leftover record from a prior
// test session in the same launch group). 500 m/s ≈ 1800 km/h — faster
// than Concorde and well above anything we'd legitimately see on a
// balloon chase, so this threshold cleanly separates signal from glitch.
const MAX_REASONABLE_MS = 500;

// Segments spanning a big time gap are treated as session boundaries —
// we can't honestly claim the vehicle travelled the straight-line
// distance across a 10-minute blackout, so we don't count it.
const MAX_SEGMENT_MS = 10 * 60 * 1000;

/**
 * Compute travel stats + rolling rate series from a telemetry track.
 *
 * Input must be ordered oldest → newest. Rows with duplicate timestamps
 * (multiple igates relaying the same packet) are collapsed to one entry
 * to stop them from distorting per-point rolling windows.
 */
export function computeTrackStats(
	raw: TelemetryCache[],
	windowMs = 120_000,
): TrackStats {
	// Dedupe by timestamp — multi-igate relays produce identical-timestamp
	// rows that differ only in `uploader_callsign`; collapsing them to one
	// sample makes the rolling-window math well-behaved.
	const points: TelemetryCache[] = [];
	let lastTs: string | null = null;
	for (const p of raw) {
		if (p.timestamp === lastTs) continue;
		points.push(p);
		lastTs = p.timestamp;
	}

	const ts = points.map((p) => Date.parse(p.timestamp));

	// ---- Cumulative distance -------------------------------------------------
	let totalKm = 0;
	let segments = 0;
	let prev: { lat: number; lon: number; t: number } | null = null;
	for (let i = 0; i < points.length; i++) {
		const p = points[i];
		if (p.lat == null || p.lon == null || !Number.isFinite(ts[i])) continue;
		if (prev != null) {
			const segSec = (ts[i] - prev.t) / 1000;
			if (segSec > 0 && segSec * 1000 <= MAX_SEGMENT_MS) {
				const segKm = haversine(prev.lat, prev.lon, p.lat, p.lon);
				const implied = (segKm * 1000) / segSec;
				if (implied <= MAX_REASONABLE_MS) {
					totalKm += segKm;
					segments++;
				}
			}
			// else: gap too long (session boundary) or implied speed absurd;
			// skip this segment silently.
		}
		prev = { lat: p.lat, lon: p.lon, t: ts[i] };
	}

	// ---- Rolling vertical rate (per point) ----------------------------------
	const verticalSeries: Array<number | null> = new Array(points.length).fill(null);
	for (let i = 0; i < points.length; i++) {
		const altI = points[i].alt;
		if (altI == null || !Number.isFinite(ts[i])) continue;
		// Walk back to the oldest point still inside the trailing window
		// that also has a valid altitude. Stop if we cross a session gap.
		let j = -1;
		for (let k = i - 1; k >= 0; k--) {
			if (!Number.isFinite(ts[k])) continue;
			if (ts[i] - ts[k] > MAX_SEGMENT_MS) break;
			if (points[k].alt == null) continue;
			j = k;
			if (ts[i] - ts[k] >= windowMs) break;
		}
		if (j < 0) continue;
		const dt = (ts[i] - ts[j]) / 1000;
		if (dt <= 0) continue;
		const rate = (altI - (points[j].alt as number)) / dt;
		if (Math.abs(rate) > MAX_REASONABLE_MS) continue;
		verticalSeries[i] = rate;
	}

	// ---- Rolling ground speed (per point) ------------------------------------
	const horizontalSeries: Array<number | null> = new Array(points.length).fill(null);
	for (let i = 0; i < points.length; i++) {
		const p = points[i];
		if (p.lat == null || p.lon == null || !Number.isFinite(ts[i])) continue;
		let j = -1;
		for (let k = i - 1; k >= 0; k--) {
			if (!Number.isFinite(ts[k])) continue;
			if (ts[i] - ts[k] > MAX_SEGMENT_MS) break;
			if (points[k].lat == null || points[k].lon == null) continue;
			j = k;
			if (ts[i] - ts[k] >= windowMs) break;
		}
		if (j < 0) continue;
		const dt = (ts[i] - ts[j]) / 1000;
		if (dt <= 0) continue;
		// Sum segments inside [j, i] honoring the same per-segment sanity
		// rules the cumulative total uses.
		let km = 0;
		let prevSeg: { lat: number; lon: number; t: number } | null = null;
		for (let k = j; k <= i; k++) {
			const q = points[k];
			if (q.lat == null || q.lon == null || !Number.isFinite(ts[k])) continue;
			if (prevSeg != null) {
				const segSec = (ts[k] - prevSeg.t) / 1000;
				if (segSec > 0 && segSec * 1000 <= MAX_SEGMENT_MS) {
					const segKm = haversine(prevSeg.lat, prevSeg.lon, q.lat, q.lon);
					const implied = (segKm * 1000) / segSec;
					if (implied <= MAX_REASONABLE_MS) km += segKm;
				}
			}
			prevSeg = { lat: q.lat, lon: q.lon, t: ts[k] };
		}
		const ms = (km * 1000) / dt;
		if (ms > MAX_REASONABLE_MS) continue;
		horizontalSeries[i] = ms;
	}

	// ---- Headline values -----------------------------------------------------
	const verticalMS = lastNonNull(verticalSeries);
	const horizontalMS = lastNonNull(horizontalSeries);

	let maxAscentMS: number | null = null;
	let maxDescentMS: number | null = null;
	for (const v of verticalSeries) {
		if (v == null) continue;
		if (v > 0 && (maxAscentMS == null || v > maxAscentMS)) maxAscentMS = v;
		if (v < 0 && (maxDescentMS == null || -v > maxDescentMS)) maxDescentMS = -v;
	}
	let maxGroundMS: number | null = null;
	for (const v of horizontalSeries) {
		if (v == null) continue;
		if (maxGroundMS == null || v > maxGroundMS) maxGroundMS = v;
	}

	return {
		totalKm: segments > 0 ? totalKm : null,
		verticalMS,
		horizontalMS,
		maxAscentMS,
		maxDescentMS,
		maxGroundMS,
		verticalSeries,
		horizontalSeries,
	};
}

function lastNonNull(vs: Array<number | null>): number | null {
	for (let i = vs.length - 1; i >= 0; i--) {
		if (vs[i] != null) return vs[i];
	}
	return null;
}
