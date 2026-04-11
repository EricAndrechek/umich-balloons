<script lang="ts">
	import type { Payload } from '$lib/types';
	import { formatAge } from '$lib/utils/format';

	let {
		payloads,
		expected,
	}: { payloads: Payload[]; expected: number | null } = $props();

	// Any payload with recent last_heard counts as reporting
	const reporting = $derived(
		payloads.filter((p) => p.last_heard && Date.now() - new Date(p.last_heard).getTime() < 10 * 60 * 1000)
	);

	const goForLaunch = $derived(expected != null && reporting.length >= expected);
</script>

<section class="p-5 rounded-lg border-2 {goForLaunch ? 'border-green-500 bg-green-50 dark:bg-green-950' : 'border-yellow-500 bg-yellow-50 dark:bg-yellow-950'}">
	<div class="flex items-center justify-between flex-wrap gap-2">
		<div>
			<h2 class="text-lg font-bold">
				{#if goForLaunch}
					✅ Go for launch
				{:else}
					⚠️ Pre-launch check
				{/if}
			</h2>
			<p class="text-sm text-slate-600 dark:text-slate-400 mt-1">
				{reporting.length} of {expected ?? '?'} expected payload{expected === 1 ? '' : 's'} reporting
			</p>
		</div>
	</div>

	{#if payloads.length > 0}
		<div class="mt-3 space-y-1">
			{#each payloads as p (p.callsign)}
				{@const isReporting = p.last_heard && Date.now() - new Date(p.last_heard).getTime() < 10 * 60 * 1000}
				<div class="flex items-center justify-between text-sm">
					<div class="flex items-center gap-2">
						<span class="w-2 h-2 rounded-full {isReporting ? 'bg-green-500' : 'bg-slate-400'}"></span>
						<span class="font-mono">{p.callsign}</span>
						<span class="text-xs text-slate-500">({p.type})</span>
					</div>
					<span class="text-xs text-slate-500">{formatAge(p.last_heard)}</span>
				</div>
			{/each}
		</div>
	{/if}
</section>
