<script lang="ts">
	import type { Payload } from '$lib/types';
	import { haversine } from '$lib/utils/haversine';
	import { formatDistance, formatAge, formatCoord } from '$lib/utils/format';
	import { googleMaps, aprsFi } from '$lib/utils/links';
	import { clock } from '$lib/stores/clock.svelte';

	let {
		station,
		balloons,
	}: { station: Payload; balloons: Payload[] } = $props();

	const distances = $derived.by(() => {
		if (station.last_lat == null || station.last_lon == null) return [];
		return balloons
			.filter((b) => b.last_lat != null && b.last_lon != null)
			.map((b) => ({
				callsign: b.callsign,
				distance: haversine(station.last_lat!, station.last_lon!, b.last_lat!, b.last_lon!),
			}))
			.sort((a, b) => a.distance - b.distance);
	});
</script>

<div class="p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
	<div class="flex items-start justify-between gap-3 mb-2">
		<h3 class="font-mono font-semibold">{station.callsign}</h3>
		<span class="text-xs text-slate-500">{formatAge(station.last_heard, clock.now)}</span>
	</div>
	{#if station.last_lat != null && station.last_lon != null}
		<a
			href={googleMaps(station.last_lat, station.last_lon)}
			target="_blank"
			rel="noopener"
			class="text-xs font-mono text-blue-600 dark:text-blue-400 hover:underline"
		>
			{formatCoord(station.last_lat)}, {formatCoord(station.last_lon)} ↗
		</a>
	{/if}

	{#if distances.length > 0}
		<div class="mt-3 space-y-1">
			<div class="text-xs text-slate-500">Distance to balloons</div>
			{#each distances as d (d.callsign)}
				<div class="flex justify-between text-sm">
					<span class="font-mono text-xs">{d.callsign}</span>
					<span class="font-semibold">{formatDistance(d.distance)}</span>
				</div>
			{/each}
		</div>
	{/if}

	<div class="flex gap-2 mt-3 text-xs">
		{#if station.last_lat != null && station.last_lon != null}
			<a
				href={googleMaps(station.last_lat, station.last_lon)}
				target="_blank"
				rel="noopener"
				class="px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
			>
				Maps
			</a>
		{/if}
		<a
			href={aprsFi(station.callsign)}
			target="_blank"
			rel="noopener"
			class="px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
		>
			aprs.fi
		</a>
	</div>
</div>
