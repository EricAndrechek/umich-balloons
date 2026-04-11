import type {
	LaunchGroupWithPayloads,
	DashboardData,
	Contact,
	TelemetryCache,
} from '$lib/types';
import { auth } from '$lib/stores/auth.svelte';

// Worker + SPA are same-origin (both served by the unified Worker with [assets]
// binding). Use relative URLs so dev (vite proxy) and prod both work.
export function apiUrl(path: string): string {
	return path;
}

export class UnauthorizedError extends Error {
	constructor(path: string) {
		super(`API ${path} unauthorized`);
		this.name = 'UnauthorizedError';
	}
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
	const h: Record<string, string> = { ...(extra ?? {}) };
	if (auth.password) h['Authorization'] = `Bearer ${auth.password}`;
	return h;
}

async function handle<T>(path: string, res: Response): Promise<T> {
	if (res.status === 401) {
		auth.clear();
		throw new UnauthorizedError(path);
	}
	if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
	return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
	// `cache: 'no-store'` keeps the browser's HTTP cache out of the picture.
	// Cloudflare rewrites our `Cache-Control: max-age=90` on the wire to the
	// zone's Browser Cache TTL (4 h default), which would otherwise cause
	// polling clients to serve stale data from their local cache for hours
	// after the first successful fetch. The Worker's `caches.default` still
	// fronts D1 on the origin side — this flag only affects the browser's
	// own cache, so edge caching is unaffected.
	const res = await fetch(apiUrl(path), {
		headers: authHeaders(),
		cache: 'no-store',
	});
	return handle<T>(path, res);
}

async function post<T>(path: string, body?: unknown): Promise<T> {
	const res = await fetch(apiUrl(path), {
		method: 'POST',
		headers: authHeaders(body ? { 'Content-Type': 'application/json' } : undefined),
		body: body ? JSON.stringify(body) : undefined,
	});
	return handle<T>(path, res);
}

async function put<T>(path: string, body: unknown): Promise<T> {
	const res = await fetch(apiUrl(path), {
		method: 'PUT',
		headers: authHeaders({ 'Content-Type': 'application/json' }),
		body: JSON.stringify(body),
	});
	return handle<T>(path, res);
}

async function del<T>(path: string): Promise<T> {
	const res = await fetch(apiUrl(path), { method: 'DELETE', headers: authHeaders() });
	return handle<T>(path, res);
}

export const api = {
	listGroups: () => get<LaunchGroupWithPayloads[]>('/api/launches'),
	listActive: () => get<LaunchGroupWithPayloads[]>('/api/launches/active'),
	listHistory: () => get<LaunchGroupWithPayloads[]>('/api/launches/history'),
	getGroup: (id: number) => get<LaunchGroupWithPayloads>(`/api/launches/${id}`),
	createGroup: (body: { name: string; base_callsigns: string[]; expected_balloon_count?: number }) =>
		post<{ id: number }>('/api/launches', body),
	updateGroup: (
		id: number,
		body: { name?: string; base_callsigns?: string[]; expected_balloon_count?: number }
	) => put<{ ok: true }>(`/api/launches/${id}`, body),
	deleteGroup: (id: number) => del<{ ok: true }>(`/api/launches/${id}`),
	start: (id: number) => post<{ ok: true; started_at: string }>(`/api/launches/${id}/start`),
	stop: (id: number) => post<{ ok: true; stopped_at: string }>(`/api/launches/${id}/stop`),
	reset: (id: number) => post<{ ok: true }>(`/api/launches/${id}/reset`),
	markLaunched: (id: number, callsign: string) =>
		post<{ ok: true; launched_at: string }>(
			`/api/launches/${id}/payloads/${encodeURIComponent(callsign)}/launched`
		),
	toggleRecovered: (id: number, callsign: string) =>
		post<{ ok: true }>(
			`/api/launches/${id}/payloads/${encodeURIComponent(callsign)}/recovered`
		),
	dashboard: (id: number) => get<DashboardData>(`/api/launches/${id}/dashboard`),
	telemetry: (
		id: number,
		opts: { callsign?: string; since?: number; limit?: number } = {},
	) => {
		const params = new URLSearchParams({ limit: String(opts.limit ?? 500) });
		if (opts.callsign) params.set('callsign', opts.callsign);
		// `since` is an `id` cursor — strictly monotonic per insert, so the
		// backend returns exactly the rows written after our last fetch.
		if (opts.since != null) params.set('since', String(opts.since));
		return get<TelemetryCache[]>(`/api/launches/${id}/telemetry?${params}`);
	},
	leaderboard: (id: number, opts: { balloon?: string; modulation?: string; limit?: number } = {}) => {
		const params = new URLSearchParams();
		if (opts.balloon) params.set('balloon', opts.balloon);
		if (opts.modulation) params.set('modulation', opts.modulation);
		params.set('limit', String(opts.limit ?? 50));
		return get<Contact[]>(`/api/launches/${id}/leaderboard?${params}`);
	},
	competition: (id: number) =>
		get<Array<{ uploader_callsign: string; balloon_callsign: string; modulation: string; total_packets: number }>>(
			`/api/launches/${id}/competition`
		),
};
