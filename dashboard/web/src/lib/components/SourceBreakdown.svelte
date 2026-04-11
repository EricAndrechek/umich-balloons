<script lang="ts">
	import type { SourceStat } from '$lib/types';

	let { stats }: { stats: SourceStat[] } = $props();

	// Okabe–Ito palette — designed to be distinguishable under the most common
	// forms of color vision deficiency (protanopia, deuteranopia, tritanopia).
	// Colors chosen to be maximally distinct from one another across all three.
	const MOD_STYLES: Record<string, { color: string; pattern: string }> = {
		APRS: { color: '#0072B2', pattern: 'solid' }, // blue, solid
		LoRa: { color: '#E69F00', pattern: 'diag' }, // orange, diagonal stripes
		Iridium: { color: '#CC79A7', pattern: 'dots' }, // reddish-purple, dots
		WSPR: { color: '#009E73', pattern: 'cross' }, // bluish-green, crosshatch
		FSK: { color: '#D55E00', pattern: 'vert' }, // vermillion, vertical lines
		unknown: { color: '#56B4E9', pattern: 'horiz' }, // sky blue, horizontal lines
	};
	const FALLBACK = { color: '#7f7f7f', pattern: 'solid' };

	function styleFor(mod: string) {
		return MOD_STYLES[mod] ?? FALLBACK;
	}

	// Group by balloon
	const byBalloon = $derived.by(() => {
		const map = new Map<string, { total: number; modulations: Map<string, number> }>();
		for (const s of stats) {
			let entry = map.get(s.balloon_callsign);
			if (!entry) {
				entry = { total: 0, modulations: new Map() };
				map.set(s.balloon_callsign, entry);
			}
			entry.total += s.packet_count;
			entry.modulations.set(s.modulation, s.packet_count);
		}
		// Sort modulations alphabetically within each entry for stable rendering
		for (const entry of map.values()) {
			entry.modulations = new Map([...entry.modulations].sort(([a], [b]) => a.localeCompare(b)));
		}
		return [...map.entries()].sort((a, b) => b[1].total - a[1].total);
	});
</script>

<section>
	<h2 class="text-lg font-semibold mb-3">Data sources</h2>
	{#if byBalloon.length === 0}
		<p class="text-sm text-slate-500">No data yet.</p>
	{:else}
		<div class="space-y-3">
			{#each byBalloon as [callsign, entry] (callsign)}
				<div class="p-3 rounded border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
					<div class="flex items-center justify-between mb-2">
						<span class="font-mono font-semibold">{callsign}</span>
						<span class="text-xs text-slate-500">{entry.total} packets total</span>
					</div>
					<!-- Stacked bar, with subtle borders between segments so the boundary is visible even when adjacent colors are similar under CVD -->
					<div class="flex h-4 rounded overflow-hidden bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700">
						{#each [...entry.modulations.entries()] as [mod, count], idx (mod)}
							{@const s = styleFor(mod)}
							<div
								class="h-full relative {idx > 0 ? 'border-l border-white dark:border-slate-900' : ''}"
								style:width="{(count / entry.total) * 100}%"
								style:background-color={s.color}
								role="img"
								aria-label="{mod}: {count} packets"
								title="{mod}: {count} packets ({((count / entry.total) * 100).toFixed(1)}%)"
							></div>
						{/each}
					</div>
					<!-- Numeric readout: color swatch + modulation name + count + percent. Even if two swatches are indistinguishable to the viewer, the text labels disambiguate. -->
					<div class="flex gap-4 mt-3 text-sm flex-wrap">
						{#each [...entry.modulations.entries()] as [mod, count] (mod)}
							{@const s = styleFor(mod)}
							{@const pct = (count / entry.total) * 100}
							<div class="flex items-center gap-2">
								<span
									class="inline-block w-3 h-3 rounded-sm border border-slate-400 dark:border-slate-600"
									style:background-color={s.color}
									aria-hidden="true"
								></span>
								<span class="font-semibold">{mod}</span>
								<span class="text-slate-600 dark:text-slate-400 tabular-nums">{count}</span>
								<span class="text-xs text-slate-500 tabular-nums">({pct.toFixed(1)}%)</span>
							</div>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</section>
