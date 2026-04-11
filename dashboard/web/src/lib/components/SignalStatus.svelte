<script lang="ts">
	import type { Payload } from '$lib/types';
	import { clock } from '$lib/stores/clock.svelte';
	import { formatAge } from '$lib/utils/format';

	let { payloads }: { payloads: Payload[] } = $props();

	// Staleness thresholds, in seconds. Different payload *types* get different
	// grace periods because balloons beacon much more often than chase stations.
	// We deliberately monitor pre-launch and landed states too — a silent tracker
	// during prep means "hold launch", and a silent tracker post-landing means
	// the recovery crew is about to lose its beacon. Only `recovered` payloads
	// are excluded from monitoring entirely.
	const THRESH_BALLOON_WARN = 2 * 60;
	const THRESH_BALLOON_ALERT = 5 * 60;
	const THRESH_STATION_WARN = 5 * 60;
	const THRESH_STATION_ALERT = 15 * 60;

	type Severity = 'ok' | 'warn' | 'alert';

	interface StatusRow {
		callsign: string;
		type: Payload['type'];
		severity: Severity;
		lastHeard: string | null;
		ageSec: number | null; // null = never heard
		label: string;
	}

	const rows = $derived.by<StatusRow[]>(() => {
		const now = clock.now;
		const out: StatusRow[] = [];
		for (const p of payloads) {
			// Only recovered payloads are excluded — we still want to know when
			// a pre-launch balloon or a landed-but-not-yet-recovered tracker
			// goes silent, since both cases are mission-critical.
			if (p.recovered) continue;

			const isBalloon = p.type === 'balloon' || p.type === 'unknown';
			const warnThresh = isBalloon ? THRESH_BALLOON_WARN : THRESH_STATION_WARN;
			const alertThresh = isBalloon ? THRESH_BALLOON_ALERT : THRESH_STATION_ALERT;

			let severity: Severity = 'ok';
			let ageSec: number | null = null;

			if (!p.last_heard) {
				// Never heard: alert loudly for balloons (mission critical, and
				// pre-launch the whole point of monitoring is to catch this),
				// warn for stations (they may just be late joining the cron).
				severity = isBalloon ? 'alert' : 'warn';
			} else {
				const heardMs = Date.parse(p.last_heard);
				if (Number.isFinite(heardMs)) {
					ageSec = Math.floor((now - heardMs) / 1000);
					if (ageSec >= alertThresh) severity = 'alert';
					else if (ageSec >= warnThresh) severity = 'warn';
				}
			}

			if (severity === 'ok') continue;

			const label = !p.last_heard
				? 'never heard'
				: `silent for ${formatAge(p.last_heard, now).replace(' ago', '')}`;

			out.push({
				callsign: p.callsign,
				type: p.type,
				severity,
				lastHeard: p.last_heard,
				ageSec,
				label,
			});
		}
		// Alerts before warnings, then alphabetical by callsign for stable order.
		out.sort((a, b) => {
			if (a.severity !== b.severity) return a.severity === 'alert' ? -1 : 1;
			return a.callsign.localeCompare(b.callsign);
		});
		return out;
	});

	const topSeverity = $derived<Severity>(
		rows.length === 0 ? 'ok' : rows.some((r) => r.severity === 'alert') ? 'alert' : 'warn',
	);
</script>

{#if rows.length > 0}
	<div
		class="mb-6 rounded-lg border-2 p-4 {topSeverity === 'alert'
			? 'border-red-500 bg-red-50 dark:bg-red-950/40'
			: 'border-amber-500 bg-amber-50 dark:bg-amber-950/40'}"
		role="alert"
		aria-live="polite"
	>
		<div class="flex items-start gap-3">
			<div
				class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-lg {topSeverity ===
				'alert'
					? 'bg-red-600 animate-pulse'
					: 'bg-amber-500'}"
				aria-hidden="true"
			>
				!
			</div>
			<div class="flex-1 min-w-0">
				<div class="font-semibold text-base {topSeverity === 'alert' ? 'text-red-900 dark:text-red-100' : 'text-amber-900 dark:text-amber-100'}">
					{topSeverity === 'alert' ? 'Signal loss detected' : 'Signal degraded'}
				</div>
				<ul class="mt-2 space-y-1 text-sm">
					{#each rows as r (r.callsign)}
						<li class="flex items-center gap-2 flex-wrap">
							<span
								class="inline-block w-2 h-2 rounded-full {r.severity === 'alert'
									? 'bg-red-600 animate-pulse'
									: 'bg-amber-500'}"
								aria-hidden="true"
							></span>
							<span class="font-mono font-semibold">{r.callsign}</span>
							<span class="text-xs uppercase px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300">
								{r.type.replace('_', ' ')}
							</span>
							<span class={r.severity === 'alert' ? 'text-red-800 dark:text-red-200' : 'text-amber-800 dark:text-amber-200'}>
								{r.label}
							</span>
						</li>
					{/each}
				</ul>
			</div>
		</div>
	</div>
{/if}
