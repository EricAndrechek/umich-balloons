import type { Context } from "hono";

/**
 * Throw this from inside a `cachedJson` compute closure to bypass
 * caching and return an error response. Useful for 404s where the
 * caller should never see a cached "not found" after the resource
 * comes into existence.
 */
export class HttpError extends Error {
	constructor(
		public status: number,
		public body: unknown,
	) {
		super(`HttpError ${status}`);
	}
}

/**
 * Edge-cache a JSON response in Cloudflare's colo cache.
 *
 * Why this is safe for our public GET endpoints: telemetry, predictions,
 * and aggregate stats only change when the cron Worker runs (every 2 min
 * via wrangler.toml). Caching responses for up to ~90 s means clients
 * never see data older than a single cron cycle. In exchange we cut D1
 * row-reads dramatically — a single /dashboard response (7 queries,
 * several full-table GROUP BYs) can read thousands of rows on a busy
 * group, and every poll from every tab pays the full bill without this.
 *
 * The cache key is the request URL, so different launch groups and
 * different delta cursors naturally land in different cache entries,
 * and every client polling the same URL within the TTL shares one
 * computation. Cache is per-colo; clients in different regions each
 * populate their own edge, which is fine — the per-group request
 * volume is low enough that this doesn't matter.
 *
 * Delta-fetch responses (`?since=...`) are intentionally NOT routed
 * through here: each client's cursor advances independently, so their
 * URLs never match and caching would just waste edge storage.
 */
export async function cachedJson<T>(
	c: Context,
	ttlSeconds: number,
	compute: () => Promise<T>,
): Promise<Response> {
	const cache = caches.default;
	// Strip any non-cacheable request state by keying only on the URL.
	const cacheKey = new Request(c.req.url, { method: "GET" });
	const hit = await cache.match(cacheKey);
	if (hit) {
		const headers = new Headers(hit.headers);
		headers.set("X-Cache", "HIT");
		return new Response(hit.body, { status: hit.status, headers });
	}

	let data: T;
	try {
		data = await compute();
	} catch (err) {
		if (err instanceof HttpError) {
			// Return the error response without caching it.
			return new Response(JSON.stringify(err.body), {
				status: err.status,
				headers: {
					"Content-Type": "application/json",
					"Cache-Control": "no-store",
					"X-Cache": "BYPASS",
				},
			});
		}
		throw err;
	}

	const body = JSON.stringify(data);
	const response = new Response(body, {
		status: 200,
		headers: {
			"Content-Type": "application/json",
			"Cache-Control": `public, max-age=${ttlSeconds}`,
			"X-Cache": "MISS",
		},
	});

	// Write-through without blocking the response. The clone is required
	// because putting a response consumes its body.
	c.executionCtx.waitUntil(cache.put(cacheKey, response.clone()));
	return response;
}
