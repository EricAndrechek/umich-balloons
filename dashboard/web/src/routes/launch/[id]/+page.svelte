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
	import { formatAge } from '$lib/utils/format';
	import { clock } from '$lib/stores/clock.svelte';
	import { payloadStatus } from '$lib/utils/status';

	const id = $derived(parseInt(page.params.id ?? '0'));

	let data = $state<DashboardData | null>(null);
	let telemetry = $state<TelemetryCache[]>([]);
	let leaderboard = $state<Contact[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let lastRefresh = $state<string | null>(null);
	let refreshTimer: ReturnType<typeof setInterval> | null = null;

	async function refresh() {
		try {
			const [d, t, lb] = await Promise.all([
				api.dashboard(id),
				api.telemetry(id, undefined, 1000),
				api.leaderboard(id, { limit: 20 }),
			]);
			data = d;
			telemetry = t;
			leaderboard = lb;
			lastRefresh = new Date().toISOString();
			error = null;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		refresh();
		refreshTimer = setInterval(refresh, 15_000);
	});

	onDestroy(() => {
		if (refreshTimer) clearInterval(refreshTimer);
	});

	const balloons = $derived(data?.payloads.filter((p) => p.type === 'balloon') ?? []);
	const stations = $derived(data?.payloads.filter((p) => p.type === 'ground_station') ?? []);
	const unknowns = $derived(data?.payloads.filter((p) => p.type === 'unknown') ?? []);

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
				{#if data.group.active}
					<span class="text-green-600 dark:text-green-400">● Active</span>
				{:else}
					<span>Inactive</span>
				{/if}
				· Started {formatAge(data.group.started_at, clock.now)}
			</p>
		</div>
		<div class="text-xs text-slate-500">
			{#if lastRefresh}Last updated {formatAge(lastRefresh, clock.now)}{/if}
			{#if error}<span class="text-red-500 ml-2">({error})</span>{/if}
		</div>
	</div>

	<!-- Signal status banner — only renders when something is stale. -->
	<SignalStatus payloads={data.payloads} />

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
						maxAlt={maxAltFor(b.callsign)}
					/>
				{/each}
			</div>
		</section>
	{/if}

	<ChaseVehiclesPanel
		stations={stations}
		balloons={balloons}
		uploaderStats={data.uploaderStats}
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
						maxAlt={maxAltFor(u.callsign)}
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
