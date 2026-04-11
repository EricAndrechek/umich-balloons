<script lang="ts">
	import type { Payload, Prediction, TelemetryCache } from '$lib/types';
	import PhaseBadge from './PhaseBadge.svelte';
	import Sparkline from './Sparkline.svelte';
	import {
		formatAltitude,
		formatCoord,
		formatAge,
		formatDuration,
		formatDistance,
	} from '$lib/utils/format';
	import { googleMaps, sondehubTracker, aprsFi, grafanaBalloon } from '$lib/utils/links';
	import { clock } from '$lib/stores/clock.svelte';
	import {
		payloadStatus,
		statusDotClass,
		statusBorderClass,
		statusTextClass,
	} from '$lib/utils/status';
	import { computeTrackStats } from '$lib/utils/track';

	let {
		payload,
		prediction,
		latest,
		sparkline,
		track,
		maxAlt,
		groupStartedAt,
	}: {
		payload: Payload;
		prediction?: Prediction;
		latest?: TelemetryCache;
		sparkline: TelemetryCache[];
		track: TelemetryCache[];
		maxAlt?: number | null;
		groupStartedAt?: string | null;
	} = $props();

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

	// Telemetry fields we surface as stat cells. Each cell shows the most
	// recent non-null value (scanning backwards through the sparkline series
	// so a temporarily missing field doesn't blank out the reading) plus a
	// mini sparkline of that field over the cached window. Altitude is
	// special-cased below because it also shows the max-altitude subtitle.
	type StatKey =
		| 'temp'
		| 'pressure'
		| 'humidity'
		| 'batt'
		| 'sats'
		| 'heading'
		| 'snr'
		| 'rssi';

	interface StatDef {
		key: StatKey;
		label: string;
		unit: string;
		digits: number;
	}

	// Note: vertical & horizontal rates are NOT in this list — we compute
	// them ourselves from the track history (see `trackStats` below) since
	// that's both smoother than the tracker's raw `vel_v`/`vel_h` readings
	// and honest about what the data actually shows. "Vertical rate" and
	// "ascent rate" are the same thing — positive = ascending, negative =
	// descending — so we only surface one cell for it.
	const STATS: StatDef[] = [
		{ key: 'temp', label: 'Temp', unit: '°C', digits: 1 },
		{ key: 'pressure', label: 'Pressure', unit: 'hPa', digits: 0 },
		{ key: 'humidity', label: 'Humidity', unit: '%', digits: 0 },
		{ key: 'batt', label: 'Battery', unit: 'V', digits: 2 },
		{ key: 'sats', label: 'Sats', unit: '', digits: 0 },
		{ key: 'heading', label: 'Heading', unit: '°', digits: 0 },
		{ key: 'snr', label: 'SNR', unit: 'dB', digits: 1 },
		{ key: 'rssi', label: 'RSSI', unit: 'dBm', digits: 0 },
	];

	// Altitude gets its own cell (with max-alt subtitle), driven off the
	// sparkline series rather than `payload.last_alt` so its graph and
	// headline value stay in sync with the rest of the grid.
	const altValues = $derived(sparkline.map((t) => (t.alt != null ? t.alt : null)));
	const altLatest = $derived.by(() => {
		for (let i = altValues.length - 1; i >= 0; i--) {
			const v = altValues[i];
			if (v != null && !Number.isNaN(v)) return v;
		}
		return payload.last_alt ?? null;
	});

	interface StatCell {
		def: StatDef;
		latest: number | null;
		values: Array<number | null>;
		hasAny: boolean;
	}

	const statCells = $derived.by<StatCell[]>(() => {
		const cells: StatCell[] = [];
		for (const def of STATS) {
			const values = sparkline.map((t) => {
				const v = t[def.key];
				return typeof v === 'number' && !Number.isNaN(v) ? v : null;
			});
			let latestVal: number | null = null;
			for (let i = values.length - 1; i >= 0; i--) {
				if (values[i] != null) {
					latestVal = values[i];
					break;
				}
			}
			// Fall back to the explicit `latest` record if the sparkline window
			// hasn't captured this field yet (e.g. cold load, short history).
			if (latestVal == null && latest) {
				const v = latest[def.key];
				if (typeof v === 'number' && !Number.isNaN(v)) latestVal = v;
			}
			const hasAny = values.some((v) => v != null) || latestVal != null;
			if (!hasAny) continue;
			cells.push({ def, latest: latestVal, values, hasAny });
		}
		return cells;
	});

	function formatStat(cell: StatCell): string {
		if (cell.latest == null) return '—';
		const v = cell.def.digits === 0 ? Math.round(cell.latest) : cell.latest;
		const num = typeof v === 'number' ? v.toFixed(cell.def.digits) : String(v);
		return cell.def.unit ? `${num} ${cell.def.unit}` : num;
	}

	// Computed travel stats — ascent/descent rate, ground speed, and
	// cumulative path length. Rolling rates are derived from whatever's
	// currently in memory (they only need a ~2 min window so the in-memory
	// cap never affects them). Cumulative totals prefer the server-side
	// authoritative values on the payload row — those are maintained by
	// the cron across the whole flight and survive page refresh or
	// in-memory telemetry pruning. During the brief window right after
	// a fresh deploy (before the cron has backfilled) the server values
	// can be null; fall back to what we can compute from in-memory track.
	const trackStats = $derived(computeTrackStats(track));

	// Prefer the server's authoritative total distance. `totalKm` from
	// the client computation is only used as a fallback when the server
	// hasn't populated the column yet — otherwise it would silently drift
	// low once older telemetry ages out of the in-memory buffer on long
	// flights.
	const displayTotalKm = $derived(
		payload.total_distance_km != null ? payload.total_distance_km : trackStats.totalKm,
	);

	// Server-maintained max altitude takes priority over the prop
	// (which comes from the legacy dashboard query). Same fallback logic
	// — either source is null, prefer whichever is non-null; if both
	// have values, the server's is canonical because it reflects every
	// sample the cron has ever seen, not just what's in memory now.
	const displayMaxAlt = $derived(
		payload.max_alt != null ? payload.max_alt : (maxAlt ?? null),
	);

	// Absolute-magnitude formatter — direction is indicated by an arrow
	// glyph in the markup, so the number itself stays unsigned.
	function formatRate(ms: number | null): string {
		if (ms == null) return '—';
		return `${Math.abs(ms).toFixed(1)} m/s`;
	}

	// Grafana link: covers the full window from when this launch group
	// started tracking through "now". We re-read clock.now inside the
	// derivation so the `to` timestamp advances as the flight progresses.
	const grafanaUrl = $derived.by(() => {
		if (!groupStartedAt) return null;
		const to = new Date(clock.now).toISOString();
		return grafanaBalloon(payload.callsign, groupStartedAt, to);
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

	<div class="mb-3">
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

	<div class="grid grid-cols-2 gap-x-3 gap-y-2 text-sm mb-3">
		<!-- Altitude is the headline cell: current reading + max subtitle + sparkline -->
		<div class="col-span-2">
			<div class="flex items-baseline justify-between gap-2">
				<div>
					<div class="text-xs text-slate-500">Altitude</div>
					<div class="font-semibold">{formatAltitude(altLatest)}</div>
				</div>
				{#if displayMaxAlt != null}
					<div class="text-right">
						<div class="text-[10px] text-slate-400 uppercase">max</div>
						<div class="text-xs font-semibold tabular-nums">{formatAltitude(displayMaxAlt)}</div>
					</div>
				{/if}
			</div>
			{#if altValues.filter((v) => v != null).length > 1}
				<div class="text-blue-600 dark:text-blue-400 mt-1">
					<Sparkline values={altValues} width={280} height={28} />
				</div>
			{/if}
		</div>

		<!-- Computed rates & total distance. Current value + max + sparkline
		     per cell; values show "—" when history is too sparse. Max ascent
		     and max descent are tracked separately since they represent
		     different phases of flight. -->
		<div class="col-span-2">
			<div class="flex items-baseline justify-between gap-2">
				<div>
					<div
						class="text-xs text-slate-500"
						title="Computed from altitude history over a trailing ~2 minute window"
					>
						Ascent rate
					</div>
					<div class="font-semibold tabular-nums">
						{#if trackStats.verticalMS != null}
							<span
								class={trackStats.verticalMS >= 0
									? 'text-blue-600 dark:text-blue-400'
									: 'text-amber-600 dark:text-amber-400'}
							>
								{trackStats.verticalMS >= 0 ? '↑' : '↓'}
							</span>
							{formatRate(trackStats.verticalMS)}
						{:else}
							—
						{/if}
					</div>
				</div>
				{#if trackStats.maxAscentMS != null || trackStats.maxDescentMS != null}
					<div class="text-right">
						<div class="text-[10px] text-slate-400 uppercase">peak</div>
						<div class="text-xs font-semibold tabular-nums">
							{#if trackStats.maxAscentMS != null}
								<span class="text-blue-600 dark:text-blue-400">↑{trackStats.maxAscentMS.toFixed(1)}</span>
							{/if}
							{#if trackStats.maxAscentMS != null && trackStats.maxDescentMS != null}
								<span class="text-slate-400"> / </span>
							{/if}
							{#if trackStats.maxDescentMS != null}
								<span class="text-amber-600 dark:text-amber-400">↓{trackStats.maxDescentMS.toFixed(1)}</span>
							{/if}
							<span class="text-slate-400"> m/s</span>
						</div>
					</div>
				{/if}
			</div>
			{#if trackStats.verticalSeries.filter((v) => v != null).length > 1}
				<div class="text-blue-600 dark:text-blue-400 mt-1">
					<Sparkline values={trackStats.verticalSeries} width={280} height={22} />
				</div>
			{/if}
		</div>

		<div class="col-span-2">
			<div class="flex items-baseline justify-between gap-2">
				<div>
					<div
						class="text-xs text-slate-500"
						title="Computed from position history over a trailing ~2 minute window"
					>
						Ground speed
					</div>
					<div class="font-semibold tabular-nums">{formatRate(trackStats.horizontalMS)}</div>
				</div>
				{#if trackStats.maxGroundMS != null}
					<div class="text-right">
						<div class="text-[10px] text-slate-400 uppercase">peak</div>
						<div class="text-xs font-semibold tabular-nums">
							{trackStats.maxGroundMS.toFixed(1)} <span class="text-slate-400">m/s</span>
						</div>
					</div>
				{/if}
			</div>
			{#if trackStats.horizontalSeries.filter((v) => v != null).length > 1}
				<div class="text-emerald-600 dark:text-emerald-400 mt-1">
					<Sparkline values={trackStats.horizontalSeries} width={280} height={22} />
				</div>
			{/if}
		</div>

		<div class="col-span-2">
			<div
				class="text-xs text-slate-500"
				title="Cumulative path length across cached telemetry. Unphysical jumps (bad GPS fixes, session boundaries) are filtered out."
			>
				Distance travelled
			</div>
			<div class="font-semibold tabular-nums">{formatDistance(displayTotalKm)}</div>
		</div>

		{#each statCells as cell (cell.def.key)}
			<div>
				<div class="text-xs text-slate-500">{cell.def.label}</div>
				<div class="font-semibold tabular-nums">{formatStat(cell)}</div>
				{#if cell.values.filter((v) => v != null).length > 1}
					<div class="text-slate-500 dark:text-slate-400">
						<Sparkline values={cell.values} width={130} height={18} />
					</div>
				{/if}
			</div>
		{/each}
	</div>

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
		{#if grafanaUrl}
			<a
				href={grafanaUrl}
				target="_blank"
				rel="noopener"
				class="px-2 py-1 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
			>
				Grafana
			</a>
		{/if}
	</div>
</div>
