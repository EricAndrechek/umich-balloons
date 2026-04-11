export function formatDistance(km: number | null | undefined): string {
	if (km == null) return '—';
	if (km < 1) return `${(km * 1000).toFixed(0)} m`;
	return `${km.toFixed(1)} km`;
}

export function formatAltitude(m: number | null | undefined): string {
	if (m == null) return '—';
	if (m >= 1000) return `${(m / 1000).toFixed(2)} km`;
	return `${m.toFixed(0)} m`;
}

export function formatCoord(value: number | null | undefined, digits = 4): string {
	if (value == null) return '—';
	return value.toFixed(digits);
}

export function formatTime(iso: string | null | undefined): string {
	if (!iso) return '—';
	try {
		return new Date(iso).toLocaleTimeString();
	} catch {
		return iso;
	}
}

export function formatDateTime(iso: string | null | undefined): string {
	if (!iso) return '—';
	try {
		return new Date(iso).toLocaleString();
	} catch {
		return iso;
	}
}

export function formatAge(iso: string | null | undefined, nowMs?: number): string {
	if (!iso) return 'never';
	const ms = (nowMs ?? Date.now()) - new Date(iso).getTime();
	if (ms < 0) return 'future';
	const s = Math.floor(ms / 1000);
	if (s < 60) return `${s}s ago`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m}m ${s % 60}s ago`;
	const h = Math.floor(m / 60);
	if (h < 24) return `${h}h ${m % 60}m ago`;
	const d = Math.floor(h / 24);
	return `${d}d ago`;
}

export function formatDuration(startIso: string | null | undefined, nowMs?: number): string {
	if (!startIso) return '—';
	const ms = (nowMs ?? Date.now()) - new Date(startIso).getTime();
	if (ms < 0) return '—';
	const s = Math.floor(ms / 1000);
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = s % 60;
	if (h > 0) return `${h}h ${m}m ${sec}s`;
	if (m > 0) return `${m}m ${sec}s`;
	return `${sec}s`;
}
