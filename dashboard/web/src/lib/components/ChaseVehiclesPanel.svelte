<script lang="ts">
	import type { Payload, UploaderStat } from '$lib/types';
	import { haversine } from '$lib/utils/haversine';
	import { formatDistance, formatAge, formatCoord } from '$lib/utils/format';
	import { googleMaps, aprsFi } from '$lib/utils/links';
	import { clock } from '$lib/stores/clock.svelte';

	let {
		stations,
		balloons,
		uploaderStats,
	}: {
		stations: Payload[];
		balloons: Payload[];
		uploaderStats: UploaderStat[];
	} = $props();

	// Same thresholds as SignalStatus — keep in sync.
	const ONLINE_SEC = 5 * 60;
	const WARN_SEC = 15 * 60;

	type Status = 'online' | 'stale' | 'offline' | 'never';

	function statusFor(lastHeard: string | null, nowMs: number): Status {
		if (!lastHeard) return 'never';
		const ms = Date.parse(lastHeard);
		if (!Number.isFinite(ms)) return 'never';
		const ageSec = (nowMs - ms) / 1000;
		if (ageSec < ONLINE_SEC) return 'online';
		if (ageSec < WARN_SEC) return 'stale';
		return 'offline';
	}

	function statusColor(s: Status): string {
		switch (s) {
			case 'online':
				return 'bg-green-500';
			case 'stale':
				return 'bg-amber-500';
			case 'offline':
				return 'bg-red-500';
			case 'never':
				return 'bg-slate-400';
		}
	}

	function statusLabel(s: Status): string {
		switch (s) {
			case 'online':
				return 'ONLINE';
			case 'stale':
				return 'STALE';
			case 'offline':
				return 'OFFLINE';
			case 'never':
				return 'NO SIGNAL';
		}
	}

	// Per-station aggregated packet count (across all balloons + modulations).
	// Used to show "how useful is this chase vehicle" at a glance.
	const statsByStation = $derived.by(() => {
		const map = new Map<string, { total: number; byBalloon: Map<string, number> }>();
		for (const s of uploaderStats) {
			let entry = map.get(s.uploader_callsign);
			if (!entry) {
				entry = { total: 0, byBalloon: new Map() };
				map.set(s.uploader_callsign, entry);
			}
			entry.total += s.packet_count;
			entry.byBalloon.set(
				s.balloon_callsign,
				(entry.byBalloon.get(s.balloon_callsign) ?? 0) + s.packet_count,
			);
		}
		return map;
	});

	interface StationRow {
		station: Payload;
		status: Status;
		totalPackets: number;
		distances: Array<{
			callsign: string;
			distance: number | null;
			packets: number;
			directlyHeard: boolean;
		}>;
	}

	const rows = $derived.by<StationRow[]>(() => {
		// We recompute on every clock tick so "last heard" ticks live.
		// eslint-disable-next-line @typescript-eslint/no-unused-expressions
		clock.now;
		const out: StationRow[] = [];
		for (const station of stations) {
			const s = statsByStation.get(station.callsign);
			const total = s?.total ?? 0;
			const distances = balloons.map((b) => {
				const packets = s?.byBalloon.get(b.callsign) ?? 0;
				let distance: number | null = null;
				if (
					station.last_lat != null &&
					station.last_lon != null &&
					b.last_lat != null &&
					b.last_lon != null
				) {
					distance = haversine(station.last_lat, station.last_lon, b.last_lat, b.last_lon);
				}
				return {
					callsign: b.callsign,
					distance,
					packets,
					directlyHeard: packets > 0,
				};
			});
			out.push({
				station,
				status: statusFor(station.last_heard, clock.now),
				totalPackets: total,
				distances,
			});
		}
		// Sort: online first, then by total packets desc
		const order: Record<Status, number> = { online: 0, stale: 1, never: 2, offline: 3 };
		out.sort((a, b) => {
			if (a.status !== b.status) return order[a.status] - order[b.status];
			return b.totalPackets - a.totalPackets;
		});
		return out;
	});
</script>

{#if rows.length > 0}
	<section class="mb-6">
		<div class="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
			<h2 class="text-lg font-semibold">Chase vehicles</h2>
			<span class="text-xs text-slate-500">
				{rows.filter((r) => r.status === 'online').length} online · {rows.length} total
			</span>
		</div>
		<div class="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
			{#each rows as r (r.station.callsign)}
				<div
					class="p-4 rounded-lg border-2 {r.status === 'online'
						? 'border-green-500/60'
						: r.status === 'stale'
							? 'border-amber-500/60'
							: r.status === 'offline'
								? 'border-red-500/60'
								: 'border-slate-300 dark:border-slate-700'} bg-white dark:bg-slate-900"
				>
					<div class="flex items-start justify-between gap-2 mb-2">
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2 mb-0.5">
								<span
									class="inline-block w-2.5 h-2.5 rounded-full {statusColor(r.status)} {r.status ===
									'online'
										? 'animate-pulse'
										: ''}"
									aria-hidden="true"
								></span>
								<h3 class="font-mono font-bold truncate">{r.station.callsign}</h3>
							</div>
							<div
								class="text-[10px] font-bold tracking-wider {r.status === 'online'
									? 'text-green-700 dark:text-green-400'
									: r.status === 'stale'
										? 'text-amber-700 dark:text-amber-400'
										: r.status === 'offline'
											? 'text-red-700 dark:text-red-400'
											: 'text-slate-500'}"
							>
								{statusLabel(r.status)}
							</div>
						</div>
						<div class="text-right text-xs text-slate-500 shrink-0">
							<div>last heard</div>
							<div class="font-semibold tabular-nums text-slate-700 dark:text-slate-300">
								{formatAge(r.station.last_heard, clock.now)}
							</div>
						</div>
					</div>

					{#if r.station.last_lat != null && r.station.last_lon != null}
						<a
							href={googleMaps(r.station.last_lat, r.station.last_lon)}
							target="_blank"
							rel="noopener"
							class="block text-xs font-mono text-blue-600 dark:text-blue-400 hover:underline mb-2"
						>
							{formatCoord(r.station.last_lat)}, {formatCoord(r.station.last_lon)} ↗
						</a>
					{:else}
						<div class="text-xs text-slate-500 italic mb-2">no position yet</div>
					{/if}

					{#if r.distances.length > 0}
						<div class="border-t border-slate-200 dark:border-slate-800 pt-2 mt-2">
							<div class="text-[10px] uppercase font-semibold text-slate-500 mb-1">
								Distance to balloons
							</div>
							<div class="space-y-1">
								{#each r.distances as d (d.callsign)}
									<div class="flex items-center justify-between gap-2 text-sm">
										<div class="flex items-center gap-1.5 min-w-0">
											{#if d.directlyHeard}
												<span
													class="inline-block w-1.5 h-1.5 rounded-full bg-green-500"
													title="Directly heard by this station"
													aria-label="Directly heard"
												></span>
											{:else}
												<span
													class="inline-block w-1.5 h-1.5 rounded-full border border-slate-400 dark:border-slate-600"
													title="Not directly heard — distance from last known positions"
													aria-label="Not directly heard"
												></span>
											{/if}
											<span class="font-mono text-xs truncate">{d.callsign}</span>
										</div>
										<div class="flex items-center gap-2 shrink-0">
											{#if d.packets > 0}
												<span class="text-xs text-slate-500 tabular-nums"
													>{d.packets} pkt{d.packets === 1 ? '' : 's'}</span
												>
											{/if}
											<span class="font-semibold tabular-nums"
												>{formatDistance(d.distance)}</span
											>
										</div>
									</div>
								{/each}
							</div>
						</div>
					{/if}

					<div class="flex items-center justify-between gap-2 mt-3 text-xs">
						<div class="text-slate-500">
							<span class="tabular-nums font-semibold text-slate-700 dark:text-slate-300"
								>{r.totalPackets}</span
							>
							packets heard
						</div>
						<div class="flex gap-1.5">
							{#if r.station.last_lat != null && r.station.last_lon != null}
								<a
									href={googleMaps(r.station.last_lat, r.station.last_lon)}
									target="_blank"
									rel="noopener"
									class="px-2 py-0.5 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
								>
									Map
								</a>
							{/if}
							<a
								href={aprsFi(r.station.callsign)}
								target="_blank"
								rel="noopener"
								class="px-2 py-0.5 rounded border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
							>
								aprs.fi
							</a>
						</div>
					</div>
				</div>
			{/each}
		</div>
	</section>
{/if}
