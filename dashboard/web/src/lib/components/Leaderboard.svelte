<script lang="ts">
	import type { Contact } from '$lib/types';
	import { formatDistance, formatAltitude, formatDateTime } from '$lib/utils/format';
	import { googleMaps, sondehubTracker, aprsFi } from '$lib/utils/links';

	let { contacts, title = 'Farthest contacts' }: { contacts: Contact[]; title?: string } = $props();
</script>

<section>
	<h2 class="text-lg font-semibold mb-3">{title}</h2>
	{#if contacts.length === 0}
		<p class="text-sm text-slate-500">
			No ranked contacts yet. Distances require both the balloon position and the uploader's
			position — LoRa-only balloons heard only by a ground station without a GPS lock won't
			appear here until the ground station knows where it is. See "Uploaders heard from" below
			for every station that has received packets, including ones with no known position.
		</p>
	{:else}
		<div class="overflow-x-auto rounded border border-slate-200 dark:border-slate-800">
			<table class="w-full text-sm">
				<thead class="bg-slate-100 dark:bg-slate-800 text-xs uppercase text-left">
					<tr>
						<th class="px-3 py-2">#</th>
						<th class="px-3 py-2">Uploader</th>
						<th class="px-3 py-2">Balloon</th>
						<th class="px-3 py-2">Mod</th>
						<th class="px-3 py-2 text-right">Distance</th>
						<th class="px-3 py-2 text-right">Alt</th>
						<th class="px-3 py-2">SNR/RSSI</th>
						<th class="px-3 py-2">Time</th>
						<th class="px-3 py-2">Links</th>
					</tr>
				</thead>
				<tbody>
					{#each contacts as c, i (c.id)}
						<tr class="border-t border-slate-200 dark:border-slate-800">
							<td class="px-3 py-2 font-semibold">{i + 1}</td>
							<td class="px-3 py-2 font-mono">{c.uploader_callsign}</td>
							<td class="px-3 py-2 font-mono">{c.balloon_callsign}</td>
							<td class="px-3 py-2">{c.modulation ?? '—'}</td>
							<td class="px-3 py-2 text-right font-semibold">{formatDistance(c.distance_km)}</td>
							<td class="px-3 py-2 text-right">{formatAltitude(c.balloon_alt)}</td>
							<td class="px-3 py-2 text-xs">
								{#if c.snr != null}SNR {c.snr.toFixed(1)}{/if}
								{#if c.rssi != null}RSSI {c.rssi.toFixed(0)}{/if}
								{#if c.snr == null && c.rssi == null}—{/if}
							</td>
							<td class="px-3 py-2 text-xs text-slate-500">{formatDateTime(c.contact_time)}</td>
							<td class="px-3 py-2 text-xs">
								<div class="flex gap-1">
									{#if c.uploader_lat != null && c.uploader_lon != null}
										<a
											href={googleMaps(c.uploader_lat, c.uploader_lon)}
											target="_blank"
											rel="noopener"
											class="text-blue-600 dark:text-blue-400 hover:underline"
										>
											map
										</a>
									{/if}
									<a
										href={sondehubTracker(c.balloon_callsign, c.balloon_lat, c.balloon_lon)}
										target="_blank"
										rel="noopener"
										class="text-blue-600 dark:text-blue-400 hover:underline"
									>
										sh
									</a>
									<a
										href={aprsFi(c.uploader_callsign)}
										target="_blank"
										rel="noopener"
										class="text-blue-600 dark:text-blue-400 hover:underline"
									>
										aprs
									</a>
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>
