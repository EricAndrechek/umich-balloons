<script lang="ts">
	import type { Payload, Prediction, TelemetryCache } from '$lib/types';
	import PhaseBadge from './PhaseBadge.svelte';
	import Sparkline from './Sparkline.svelte';
	import { formatAltitude, formatCoord, formatAge, formatDuration } from '$lib/utils/format';
	import { googleMaps, sondehubTracker, aprsFi } from '$lib/utils/links';
	import { clock } from '$lib/stores/clock.svelte';
	import {
		payloadStatus,
		statusDotClass,
		statusBorderClass,
		statusTextClass,
	} from '$lib/utils/status';

	let {
		payload,
		prediction,
		latest,
		sparkline,
		maxAlt,
	}: {
		payload: Payload;
		prediction?: Prediction;
		latest?: TelemetryCache;
		sparkline: TelemetryCache[];
		maxAlt?: number | null;
	} = $props();

	const altSeries = $derived(sparkline.map((t) => t.alt));
	const battSeries = $derived(sparkline.map((t) => t.batt));

	const status = $derived(payloadStatus(payload, clock.now));

	// Derive the burst point (apex of predicted trajectory) from the
	// prediction's trajectory JSON if available. The trajectory is an array
	// of { time, lat, lon, alt } points; the max-altitude point is the burst.
	const burstPoint = $derived.by(() => {
		if (!prediction?.trajectory_json) return null;
		try {
			const pts = JSON.parse(prediction.trajectory_json) as Array<{
				time: number;
				lat: number;
				lon: number;
				alt: number;
			}>;
			if (!Array.isArray(pts) || pts.length === 0) return null;
			let best = pts[0];
			for (const p of pts) if (p.alt > best.alt) best = p;
			return best;
		} catch {
			return null;
		}
	});
</script>

<div
	class="p-4 rounded-lg border-2 {statusBorderClass(status.kind)} bg-white dark:bg-slate-900"
>
	<div class="flex items-start justify-between gap-3 mb-3">
		<div class="min-w-0 flex-1">
			<div class="flex items-center gap-2 mb-0.5">
				<span
					class="inline-block w-2.5 h-2.5 rounded-full {statusDotClass(status.kind)} {status.kind ===
					'online'
						? 'animate-pulse'
						: ''}"
					aria-hidden="true"
				></span>
				<h3 class="font-mono font-bold text-lg truncate">{payload.callsign}</h3>
			</div>
			<div
				class="text-[10px] font-bold tracking-wider mb-1 {statusTextClass(status.kind)}"
			>
				{status.label}
			</div>
			<div class="flex items-center gap-2 flex-wrap">
				<PhaseBadge phase={payload.phase} />
				{#if payload.recovered}
					<span class="text-xs font-semibold px-2 py-0.5 rounded bg-green-600 text-white">
						recovered
					</span>
				{/if}
				{#if payload.launched_at}
					<span class="text-xs text-slate-500">T+{formatDuration(payload.launched_at, clock.now)}</span>
				{/if}
			</div>
		</div>
		<div class="text-right text-xs text-slate-500 shrink-0">
			<div>last heard</div>
			<div class="font-semibold tabular-nums text-slate-700 dark:text-slate-300">
				{formatAge(payload.last_heard, clock.now)}
			</div>
		</div>
	</div>

	<div class="grid grid-cols-2 gap-2 text-sm mb-3">
		<div>
			<div class="text-xs text-slate-500">Altitude</div>
			<div class="font-semibold">{formatAltitude(payload.last_alt)}</div>
		</div>
		<div>
			<div class="text-xs text-slate-500">Max altitude</div>
			<div class="font-semibold">{formatAltitude(maxAlt)}</div>
		</div>
		<div class="col-span-2">
			<div class="text-xs text-slate-500">Current position</div>
			{#if payload.last_lat != null && payload.last_lon != null}
				<a
					href={googleMaps(payload.last_lat, payload.last_lon)}
					target="_blank"
					rel="noopener"
					class="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline"
				>
					{formatCoord(payload.last_lat)}, {formatCoord(payload.last_lon)} ↗
				</a>
			{:else}
				<div class="font-mono text-xs">—</div>
			{/if}
		</div>
		{#if latest}
			{#if latest.temp != null}
				<div>
					<div class="text-xs text-slate-500">Temperature</div>
					<div class="font-semibold">{latest.temp.toFixed(1)}°C</div>
				</div>
			{/if}
			{#if latest.batt != null}
				<div>
					<div class="text-xs text-slate-500">Battery</div>
					<div class="font-semibold">{latest.batt.toFixed(2)} V</div>
				</div>
			{/if}
			{#if latest.pressure != null}
				<div>
					<div class="text-xs text-slate-500">Pressure</div>
					<div class="font-semibold">{latest.pressure.toFixed(0)} hPa</div>
				</div>
			{/if}
			{#if latest.humidity != null}
				<div>
					<div class="text-xs text-slate-500">Humidity</div>
					<div class="font-semibold">{latest.humidity.toFixed(0)}%</div>
				</div>
			{/if}
			{#if latest.sats != null}
				<div>
					<div class="text-xs text-slate-500">Sats</div>
					<div class="font-semibold">{latest.sats}</div>
				</div>
			{/if}
			{#if latest.vel_v != null}
				<div>
					<div class="text-xs text-slate-500">Vertical vel.</div>
					<div class="font-semibold">{latest.vel_v.toFixed(1)} m/s</div>
				</div>
			{/if}
		{/if}
	</div>

	{#if sparkline.length > 1}
		<div class="flex items-center gap-4 mb-3 text-xs">
			<div class="flex items-center gap-2">
				<span class="text-slate-500">Alt</span>
				<span class="text-blue-600 dark:text-blue-400">
					<Sparkline values={altSeries} />
				</span>
			</div>
			{#if battSeries.some((b) => b != null)}
				<div class="flex items-center gap-2">
					<span class="text-slate-500">Batt</span>
					<span class="text-green-600 dark:text-green-400">
						<Sparkline values={battSeries} />
					</span>
				</div>
			{/if}
		</div>
	{/if}

	{#if prediction}
		<div class="border-t border-slate-200 dark:border-slate-800 pt-3 mb-3">
			<div class="flex items-center justify-between mb-2">
				<div class="text-xs font-semibold uppercase text-slate-500">Predicted flight path</div>
				<div class="text-[10px] text-slate-400">
					updated {formatAge(prediction.updated_at, clock.now)}
				</div>
			</div>

			{#if prediction.ascent_rate != null || prediction.descent_rate != null}
				<div class="flex gap-3 text-xs text-slate-500 mb-2">
					{#if prediction.ascent_rate != null}
						<span>↑ {prediction.ascent_rate.toFixed(1)} m/s</span>
					{/if}
					{#if prediction.descent_rate != null}
						<span>↓ {prediction.descent_rate.toFixed(1)} m/s</span>
					{/if}
				</div>
			{/if}

			<div class="space-y-2 text-xs">
				{#if prediction.burst_altitude != null}
					<div class="flex items-start justify-between gap-2">
						<div>
							<div class="text-slate-500">Burst (apex)</div>
							<div class="font-semibold">{formatAltitude(prediction.burst_altitude)}</div>
						</div>
						{#if burstPoint}
							<a
								href={googleMaps(burstPoint.lat, burstPoint.lon)}
								target="_blank"
								rel="noopener"
								class="px-2 py-1 rounded border border-blue-500 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-950 font-mono whitespace-nowrap"
							>
								{formatCoord(burstPoint.lat, 3)}, {formatCoord(burstPoint.lon, 3)} ↗
							</a>
						{/if}
					</div>
				{/if}

				{#if prediction.predicted_lat != null && prediction.predicted_lon != null}
					<div class="flex items-start justify-between gap-2">
						<div>
							<div class="text-slate-500">Landing (touchdown)</div>
							<div class="font-semibold">
								{#if prediction.predicted_alt != null}
									{formatAltitude(prediction.predicted_alt)}
								{:else}
									ground
								{/if}
							</div>
						</div>
						<a
							href={googleMaps(prediction.predicted_lat, prediction.predicted_lon)}
							target="_blank"
							rel="noopener"
							class="px-2 py-1 rounded border border-blue-500 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-950 font-mono whitespace-nowrap"
						>
							{formatCoord(prediction.predicted_lat, 3)}, {formatCoord(prediction.predicted_lon, 3)} ↗
						</a>
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<div class="flex flex-wrap gap-2 text-xs">
		<a
			href={sondehubTracker(payload.callsign, payload.last_lat, payload.last_lon)}
			target="_blank"
			rel="noopener"
			class="px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
		>
			SondeHub
		</a>
		<a
			href={aprsFi(payload.callsign)}
			target="_blank"
			rel="noopener"
			class="px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
		>
			aprs.fi
		</a>
	</div>
</div>
