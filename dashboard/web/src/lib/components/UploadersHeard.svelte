<script lang="ts">
	import type { UploaderHeard } from '$lib/types';
	import { formatDistance, formatAge } from '$lib/utils/format';
	import { aprsFi } from '$lib/utils/links';
	import { clock } from '$lib/stores/clock.svelte';

	let { uploaders }: { uploaders: UploaderHeard[] } = $props();
</script>

<section>
	<h2 class="text-lg font-semibold mb-3">Uploaders heard from</h2>
	{#if uploaders.length === 0}
		<p class="text-sm text-slate-500">
			No uploads received yet. The cron fetches every 2 minutes — if a balloon or ground station has
			been transmitting and a SondeHub uploader has heard it, it will appear here.
		</p>
	{:else}
		<div class="overflow-x-auto rounded border border-slate-200 dark:border-slate-800">
			<table class="w-full text-sm">
				<thead class="bg-slate-100 dark:bg-slate-800 text-xs uppercase text-left">
					<tr>
						<th class="px-3 py-2">Uploader</th>
						<th class="px-3 py-2 text-right">Packets</th>
						<th class="px-3 py-2 text-right">Best distance</th>
						<th class="px-3 py-2">Last contact</th>
						<th class="px-3 py-2"></th>
					</tr>
				</thead>
				<tbody>
					{#each uploaders as u (u.uploader_callsign)}
						<tr class="border-t border-slate-200 dark:border-slate-800">
							<td class="px-3 py-2 font-mono">{u.uploader_callsign}</td>
							<td class="px-3 py-2 text-right">{u.contact_count}</td>
							<td class="px-3 py-2 text-right font-semibold">
								{#if u.best_distance_km != null}
									{formatDistance(u.best_distance_km)}
								{:else}
									<span class="text-slate-400 text-xs font-normal" title="Uploader position unknown">
										no position
									</span>
								{/if}
							</td>
							<td class="px-3 py-2 text-xs text-slate-500">
								{formatAge(u.last_contact_time, clock.now)}
							</td>
							<td class="px-3 py-2 text-xs">
								<a
									href={aprsFi(u.uploader_callsign)}
									target="_blank"
									rel="noopener"
									class="text-blue-600 dark:text-blue-400 hover:underline"
								>
									aprs.fi
								</a>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<p class="mt-2 text-xs text-slate-500">
			"No position" means we know this station heard the balloon but don't yet know where the
			station is, so we can't compute a distance. For LoRa-only balloons this is normal until your
			ground station gets a GPS lock.
		</p>
	{/if}
</section>
