<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import { RESOURCE_LINKS } from '$lib/utils/links';
	import { formatAge, formatDuration } from '$lib/utils/format';
	import type { LaunchGroupWithPayloads } from '$lib/types';

	let active = $state<LaunchGroupWithPayloads[]>([]);
	let history = $state<LaunchGroupWithPayloads[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	function formatShortDate(iso: string | null): string {
		if (!iso) return '—';
		try {
			return new Date(iso).toLocaleDateString(undefined, {
				year: 'numeric',
				month: 'short',
				day: 'numeric',
			});
		} catch {
			return iso;
		}
	}

	onMount(async () => {
		try {
			// Two independent fetches — fan out so a slow one doesn't block the
			// other. Both endpoints are public + edge-cached so this is cheap.
			const [a, h] = await Promise.all([api.listActive(), api.listHistory()]);
			active = a;
			history = h;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Unknown error';
		} finally {
			loading = false;
		}
	});
</script>

{#if loading}
	<p class="text-slate-500">Loading…</p>
{:else if error}
	<p class="text-red-500">Error: {error}</p>
{:else}
	{#if active.length > 0}
		<section class="mb-8">
			<h1 class="text-2xl font-bold mb-4">🚀 Active Launches</h1>
			<div class="space-y-3">
				{#each active as group (group.id)}
					<a
						href="/launch/{group.id}"
						class="block p-5 rounded-lg border-2 border-green-500 bg-green-50 dark:bg-green-950 dark:border-green-700 hover:shadow-lg transition"
					>
						<div class="flex items-start justify-between gap-4">
							<div>
								<div class="flex items-center gap-2">
									<span class="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
									<h2 class="text-xl font-semibold">{group.name}</h2>
								</div>
								<p class="text-sm text-slate-600 dark:text-slate-400 mt-1">
									Started {formatAge(group.started_at)} · {group.payloads.length} payload{group.payloads.length === 1 ? '' : 's'} discovered
								</p>
								<p class="text-xs text-slate-500 mt-1">
									Tracking: {group.base_callsigns.join(', ')}
								</p>
							</div>
							<span class="text-2xl">→</span>
						</div>
					</a>
				{/each}
			</div>
		</section>
	{:else}
		<section class="mb-8">
			<div class="p-8 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 text-center">
				<p class="text-slate-500">No active launches right now.</p>
				<p class="text-sm text-slate-400 mt-1">
					Visit <a href="/admin" class="text-blue-600 dark:text-blue-400 hover:underline">admin</a>
					to start tracking a launch group.
				</p>
			</div>
		</section>
	{/if}

	{#if history.length > 0}
		<section class="mb-8">
			<h2 class="text-xl font-bold mb-4">Past Launches</h2>
			<div class="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
				{#each history as group (group.id)}
					{@const duration =
						group.started_at && group.stopped_at
							? formatDuration(group.started_at, Date.parse(group.stopped_at))
							: null}
					<a
						href="/launch/{group.id}"
						class="block p-4 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-blue-400 dark:hover:border-blue-600 hover:shadow transition"
					>
						<div class="flex items-start justify-between gap-2">
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2">
									<span
										class="inline-block w-2 h-2 rounded-full bg-slate-400"
										aria-hidden="true"
									></span>
									<h3 class="font-semibold truncate">{group.name}</h3>
								</div>
								<p class="text-xs text-slate-500 mt-1">
									{formatShortDate(group.started_at)}
									{#if duration} · {duration}{/if}
								</p>
								<p class="text-xs text-slate-400 mt-0.5">
									{group.payloads.length} payload{group.payloads.length === 1 ? '' : 's'}
								</p>
							</div>
							<span class="text-slate-400 shrink-0">→</span>
						</div>
					</a>
				{/each}
			</div>
		</section>
	{/if}

	<section>
		<h2 class="text-xl font-bold mb-4">Resources</h2>
		<div class="grid sm:grid-cols-2 md:grid-cols-3 gap-3">
			{#each RESOURCE_LINKS as link (link.url)}
				<a
					href={link.url}
					target="_blank"
					rel="noopener"
					class="block p-4 rounded border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-blue-400 dark:hover:border-blue-600 hover:shadow"
				>
					<div class="font-semibold">{link.label}</div>
					<div class="text-sm text-slate-500 mt-1">{link.desc}</div>
				</a>
			{/each}
		</div>
	</section>
{/if}
