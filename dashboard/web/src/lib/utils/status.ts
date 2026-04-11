import type { Payload } from '$lib/types';

export type SignalKind = 'online' | 'stale' | 'offline' | 'never' | 'quiet';

export interface PayloadStatus {
	kind: SignalKind;
	/** Short label like "ONLINE" / "PRE-LAUNCH" / "RECOVERED". */
	label: string;
	/** True when the payload is in a state we actively expect telemetry from. */
	monitored: boolean;
}

// Thresholds in seconds. Balloons are expected to beacon much more often than
// chase vehicles, so their online/stale window is tighter. Keep in sync with
// the alert thresholds in SignalStatus.svelte — these are the "soft" bands
// (green→amber→red) rather than the warn/alert escalation.
const BALLOON_ONLINE = 2 * 60;
const BALLOON_STALE = 5 * 60;
const STATION_ONLINE = 5 * 60;
const STATION_STALE = 15 * 60;

/**
 * Classify a payload's current signal state based on last_heard.
 *
 * Only `recovered` is treated as "quiet" — pre-launch balloons and landed
 * balloons are still actively monitored so we notice silent trackers during
 * prep (hold launch) and after touchdown (before recovery crew reaches it).
 */
export function payloadStatus(p: Payload, nowMs: number): PayloadStatus {
	if (p.recovered) return { kind: 'quiet', label: 'RECOVERED', monitored: false };

	const isBalloon = p.type === 'balloon' || p.type === 'unknown';
	const onlineThresh = isBalloon ? BALLOON_ONLINE : STATION_ONLINE;
	const staleThresh = isBalloon ? BALLOON_STALE : STATION_STALE;

	if (!p.last_heard) return { kind: 'never', label: 'NO SIGNAL', monitored: true };
	const heardMs = Date.parse(p.last_heard);
	if (!Number.isFinite(heardMs)) return { kind: 'never', label: 'NO SIGNAL', monitored: true };

	const ageSec = (nowMs - heardMs) / 1000;
	if (ageSec < onlineThresh) return { kind: 'online', label: 'ONLINE', monitored: true };
	if (ageSec < staleThresh) return { kind: 'stale', label: 'STALE', monitored: true };
	return { kind: 'offline', label: 'OFFLINE', monitored: true };
}

export function statusDotClass(k: SignalKind): string {
	switch (k) {
		case 'online':
			return 'bg-green-500';
		case 'stale':
			return 'bg-amber-500';
		case 'offline':
			return 'bg-red-500';
		case 'never':
			return 'bg-red-500';
		case 'quiet':
			return 'bg-slate-400';
	}
}

export function statusBorderClass(k: SignalKind): string {
	switch (k) {
		case 'online':
			return 'border-green-500/60';
		case 'stale':
			return 'border-amber-500/60';
		case 'offline':
		case 'never':
			return 'border-red-500/60';
		case 'quiet':
			return 'border-slate-300 dark:border-slate-700';
	}
}

export function statusTextClass(k: SignalKind): string {
	switch (k) {
		case 'online':
			return 'text-green-700 dark:text-green-400';
		case 'stale':
			return 'text-amber-700 dark:text-amber-400';
		case 'offline':
		case 'never':
			return 'text-red-700 dark:text-red-400';
		case 'quiet':
			return 'text-slate-500';
	}
}
