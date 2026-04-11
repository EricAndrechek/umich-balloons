<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/client';
	import type { DashboardData, Contact, TelemetryCache } from '$lib/types';
	import GoForLaunch from '$lib/components/GoForLaunch.svelte';
	import BalloonCard from '$lib/components/BalloonCard.svelte';
	import ChaseVehiclesPanel from '$lib/components/ChaseVehiclesPanel.svelte';
	import SignalStatus from '$lib/components/SignalStatus.svelte';
	import Leaderboard from '$lib/components/Leaderboard.svelte';
	import SourceBreakdown from '$lib/components/SourceBreakdown.svelte';
	import UploadersHeard from '$lib/components/UploadersHeard.svelte';
	import { formatAge, formatDateTime, formatDuration } from '$lib/utils/format';
	import { clock } from '$lib/stores/clock.svelte';
	import { payloadStatus } from '$lib/utils/status';

	const id = $derived(parseInt(page.params.id ?? '0'));

	// Poll cadence (ms). The Worker fans out its 1-min cron into 3 sub-polls
	// at 20s intervals, so SondeHub data lands in D1 every ~20s. Polling at
	// 10s here means we never wait more than ~half a sub-poll cycle to see
	// fresh data, while edge-cache (5s TTL) absorbs the request volume so
	// most polls don't even reach D1.
	const POLL_INTERVAL_MS = 10_000;

	// If the tab is hidden longer than this, we can't trust the in-memory
	// delta cursor — the row count between `since` and now may exceed the
	// delta response's safety limit, and timestamps may have lapsed
	// further than the server cares to remember. Force a full refetch on
	// resume instead of risking a silent gap.
	const STALE_RESUME_MS = 60_000;

	// Cap the in-memory telemetry series so it can't grow unbounded over
	// a multi-hour flight. 10000 points is a comfortable ceiling for the
	// raw in-memory track even on an all-day flight with multiple balloons
	// beaconing fast; the headline totals (max_alt, total_distance_km)
	// come from server-side cached columns on the payload row and so
	// survive any pruning here.
	const MAX_TELEMETRY_POINTS = 10000;

	let data = $state<DashboardData | null>(null);
	let telemetry = $state<TelemetryCache[]>([]);
	let leaderboard = $state<Contact[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let lastRefresh = $state<string | null>(null);
	// ms timestamp of the most recent successful refresh — distinct from
	// `lastRefresh` (ISO, used only for the "last updated" display) so we
	// can compute resume staleness without reparsing.
	let lastRefreshMs = $state(0);
	// Cursor for delta telemetry fetches — the largest `id` we've seen.
	// `null` means "next fetch is a full fetch" (cold start, post-error,
	// post-long-hidden).
	let telemetryCursor: number | null = null;
	let refreshTimer: ReturnType<typeof setInterval> | null = null;
	let inFlight = false;

	async function refresh({ force = false }: { force?: boolean } = {}) {
		// Guard against overlapping fetches — if the network is slow and
		// the poll timer fires while the previous request is still in
		// flight, drop the new tick instead of piling on.
		if (inFlight) return;
		inFlight = true;
		try {
			const needFull = force || telemetryCursor == null;
			const telemetryPromise = needFull
				? api.telemetry(id, { limit: 1000 })
				: api.telemetry(id, { since: telemetryCursor!, limit: 1000 });

			const [d, t, lb] = await Promise.all([
				api.dashboard(id),
				telemetryPromise,
				api.leaderboard(id, { limit: 20 }),
			]);
			data = d;

			if (needFull) {
				telemetry = t;
			} else if (t.length > 0) {
				// Append delta and re-sort by timestamp. Server already
				// orders delta by timestamp ASC, but on edge cases
				// (late-arriving records inserted out of order relative
				// to the tail we already have) a re-sort is cheap
				// insurance. ISO-8601 strings sort chronologically.
				const merged = telemetry.concat(t);
				merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
				telemetry =
					merged.length > MAX_TELEMETRY_POINTS
						? merged.slice(-MAX_TELEMETRY_POINTS)
						: merged;
			}

			// Advance cursor to the max `id` in our current view, covering
			// both the full-fetch and delta-fetch cases in one sweep.
			let maxId = telemetryCursor ?? 0;
			for (const row of telemetry) {
				if (row.id > maxId) maxId = row.id;
			}
			telemetryCursor = maxId;

			leaderboard = lb;
			lastRefresh = new Date().toISOString();
			lastRefreshMs = Date.now();
			error = null;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			// On failure, drop the cursor so the next attempt does a
			// full refetch. Cheaper than risking a silent gap if the
			// error was caused by a cursor the server no longer honors.
			telemetryCursor = null;
		} finally {
			inFlight = false;
			loading = false;
		}
	}

	function startPolling() {
		if (refreshTimer != null) return;
		// Archived launches don't change — no point polling. The first
		// fetch on mount has already loaded everything we'll ever show.
		if (data && !data.group.active) return;
		refreshTimer = setInterval(() => refresh(), POLL_INTERVAL_MS);
	}

	function stopPolling() {
		if (refreshTimer != null) {
			clearInterval(refreshTimer);
			refreshTimer = null;
		}
	}

	function handleVisibility() {
		if (document.hidden) {
			// Pause polling entirely — a hidden tab has no reason to
			// consume Worker requests, D1 reads, cellular data, or
			// phone battery.
			stopPolling();
		} else {
			// Resume. If we've been hidden long enough that our delta
			// cursor is untrustworthy, force a full refetch so we don't
			// silently miss rows. Then restart the interval (no-op for
			// archived launches).
			if (data && !data.group.active) return;
			const idleMs = Date.now() - lastRefreshMs;
			refresh({ force: idleMs > STALE_RESUME_MS });
			startPolling();
		}
	}

	onMount(async () => {
		await refresh({ force: true });
		// Only attach the polling timer + visibility listener for live
		// launches. Archived launches are static so the timer would do
		// nothing and the visibility hook would just run dead refreshes.
		if (data && data.group.active) {
			startPolling();
			document.addEventListener('visibilitychange', handleVisibility);
		}
	});

	onDestroy(() => {
		stopPolling();
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibility);
		}
	});

	const balloons = $derived(data?.payloads.filter((p) => p.type === 'balloon') ?? []);
	const stations = $derived(data?.payloads.filter((p) => p.type === 'ground_station') ?? []);
	const unknowns = $derived(data?.payloads.filter((p) => p.type === 'unknown') ?? []);

	// An archived/historic launch — stopped, never coming back. We hide
	// every "live" affordance: the signal-loss banner, last-heard tickers,
	// the relative "started X ago" header, the "last updated" footer, and
	// the polling timer itself. The cards still show position, max alt,
	// total distance, and predicted-landing data because those are the
	// historical record of the flight.
	const archived = $derived(data != null && !data.group.active);

	// "X online / Y total" summary counts in-flight balloons that are
	// currently beaconing. Pre-launch / landed / recovered balloons are
	// excluded from the "monitored" pool so the ratio reflects active flight.
	const balloonSummary = $derived.by(() => {
		// Reactive dep on clock so counts tick live alongside the cards.
		// eslint-disable-next-line @typescript-eslint/no-unused-expressions
		clock.now;
		let online = 0;
		let monitored = 0;
		for (const b of balloons) {
			const s = payloadStatus(b, clock.now);
			if (!s.monitored) continue;
			monitored++;
			if (s.kind === 'online') online++;
		}
		return { online, monitored, total: balloons.length };
	});

	function predictionFor(callsign: string) {
		return data?.predictions.find((p) => p.balloon_callsign === callsign);
	}

	function latestFor(callsign: string) {
		return data?.latestTelemetry.find((t) => t.callsign === callsign);
	}

	function maxAltFor(callsign: string): number | null {
		return data?.maxAltitudes.find((m) => m.callsign === callsign)?.max_alt ?? null;
	}

	function sparklineFor(callsign: string): TelemetryCache[] {
		return telemetry.filter((t) => t.callsign === callsign).slice(-50);
	}

	// Full (unsliced) track for a callsign — used for computed rates and
	// cumulative distance totals where we want every sample we have, not
	// just the recent sparkline window.
	function trackFor(callsign: string): TelemetryCache[] {
		return telemetry.filter((t) => t.callsign === callsign);
	}
</script>

{#if loading}
	<p class="text-slate-500">Loading…</p>
{:else if error && !data}
	<p class="text-red-500">Error: {error}</p>
{:else if data}
	<div class="flex items-baseline justify-between flex-wrap gap-3 mb-6">
		<div>
			<h1 class="text-2xl font-bold">{data.group.name}</h1>
			<p class="text-sm text-slate-500">
				{#if archived}
					<span
						class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-400 align-middle"
					>
						ARCHIVED
					</span>
					{#if data.group.started_at && data.group.stopped_at}
						· {formatDateTime(data.group.started_at)} → {formatDateTime(data.group.stopped_at)}
						· duration {formatDuration(
							data.group.started_at,
							Date.parse(data.group.stopped_at),
						)}
					{/if}
				{:else}
					<span class="text-green-600 dark:text-green-400">● Active</span>
					· Started {formatAge(data.group.started_at, clock.now)}
				{/if}
			</p>
		</div>
		{#if !archived}
			<div class="text-xs text-slate-500">
				{#if lastRefresh}Last updated {formatAge(lastRefresh, clock.now)}{/if}
				{#if error}<span class="text-red-500 ml-2">({error})</span>{/if}
			</div>
		{/if}
	</div>

	<!-- Signal status banner — only renders when something is stale, and
	     never on archived launches (where everything is "stale" forever). -->
	{#if !archived}
		<SignalStatus payloads={data.payloads} />
	{/if}

	{#if data.group.expected_balloon_count != null && balloons.every((b) => !b.launched_at)}
		<div class="mb-6">
			<GoForLaunch payloads={data.payloads} expected={data.group.expected_balloon_count} />
		</div>
	{/if}

	{#if balloons.length > 0}
		<section class="mb-6">
			<div class="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
				<h2 class="text-lg font-semibold">Balloons</h2>
				<span class="text-xs text-slate-500">
					{#if balloonSummary.monitored > 0}
						{balloonSummary.online} online · {balloonSummary.total} total
					{:else}
						{balloonSummary.total} total
					{/if}
				</span>
			</div>
			<div class="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
				{#each balloons as b (b.callsign)}
					<BalloonCard
						payload={b}
						prediction={predictionFor(b.callsign)}
						latest={latestFor(b.callsign)}
						sparkline={sparklineFor(b.callsign)}
						track={trackFor(b.callsign)}
						maxAlt={maxAltFor(b.callsign)}
						groupStartedAt={data.group.started_at}
						{archived}
					/>
				{/each}
			</div>
		</section>
	{/if}

	<ChaseVehiclesPanel
		stations={stations}
		balloons={balloons}
		uploaderStats={data.uploaderStats}
		telemetry={telemetry}
		{archived}
	/>

	{#if unknowns.length > 0}
		<section class="mb-6">
			<h2 class="text-lg font-semibold mb-3">Unclassified</h2>
			<p class="text-xs text-slate-500 mb-2">
				Callsigns heard but not yet auto-classified. Promote to balloon or mark as recovered from
				the admin page.
			</p>
			<div class="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
				{#each unknowns as u (u.callsign)}
					<BalloonCard
						payload={u}
						prediction={predictionFor(u.callsign)}
						latest={latestFor(u.callsign)}
						sparkline={sparklineFor(u.callsign)}
						track={trackFor(u.callsign)}
						maxAlt={maxAltFor(u.callsign)}
						groupStartedAt={data.group.started_at}
						{archived}
					/>
				{/each}
			</div>
		</section>
	{/if}

	<div class="mb-6">
		<Leaderboard contacts={leaderboard} />
	</div>

	<div class="mb-6">
		<UploadersHeard uploaders={data.uploadersHeard} />
	</div>

	<div class="mb-6">
		<SourceBreakdown stats={data.sourceStats} />
	</div>
{/if}
