<script lang="ts">
	import { onMount } from 'svelte';
	import { api, UnauthorizedError } from '$lib/api/client';
	import { formatDateTime, formatAge } from '$lib/utils/format';
	import type { LaunchGroupWithPayloads, Payload } from '$lib/types';
	import { clock } from '$lib/stores/clock.svelte';
	import { auth } from '$lib/stores/auth.svelte';

	let groups = $state<LaunchGroupWithPayloads[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Login form
	let pwInput = $state('');
	let loginError = $state<string | null>(null);
	let loggingIn = $state(false);

	// New group form
	let newName = $state('');
	let newCallsigns = $state('');
	let newCount = $state<number | null>(null);
	let creating = $state(false);

	// Edit form (only one group editable at a time)
	let editingId = $state<number | null>(null);
	let editName = $state('');
	let editCallsigns = $state('');
	let editCount = $state<number | null>(null);
	let savingEdit = $state(false);

	async function refresh() {
		try {
			groups = await api.listGroups();
			error = null;
		} catch (err) {
			if (err instanceof UnauthorizedError) {
				// auth.clear() already called inside the client; let the template
				// fall through to the login form.
				return;
			}
			error = err instanceof Error ? err.message : String(err);
		}
	}

	onMount(async () => {
		if (auth.password) {
			await refresh();
		}
		loading = false;
	});

	async function login(e: Event) {
		e.preventDefault();
		if (!pwInput) return;
		loggingIn = true;
		loginError = null;
		auth.set(pwInput);
		try {
			groups = await api.listGroups();
			pwInput = '';
		} catch (err) {
			if (err instanceof UnauthorizedError) {
				loginError = 'Incorrect password.';
			} else {
				loginError = err instanceof Error ? err.message : String(err);
			}
		} finally {
			loggingIn = false;
		}
	}

	function logout() {
		auth.clear();
		groups = [];
	}

	async function createGroup(e: Event) {
		e.preventDefault();
		if (!newName.trim() || !newCallsigns.trim()) return;
		creating = true;
		try {
			await api.createGroup({
				name: newName.trim(),
				base_callsigns: newCallsigns
					.split(/[,\s]+/)
					.map((s) => s.trim())
					.filter(Boolean),
				expected_balloon_count: newCount ?? undefined,
			});
			newName = '';
			newCallsigns = '';
			newCount = null;
			await refresh();
		} catch (err) {
			alert(`Error: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			creating = false;
		}
	}

	function startEdit(g: LaunchGroupWithPayloads) {
		editingId = g.id;
		editName = g.name;
		editCallsigns = g.base_callsigns.join(', ');
		editCount = g.expected_balloon_count ?? null;
	}

	function cancelEdit() {
		editingId = null;
		editName = '';
		editCallsigns = '';
		editCount = null;
	}

	async function saveEdit(id: number) {
		if (!editName.trim() || !editCallsigns.trim()) return;
		savingEdit = true;
		try {
			await api.updateGroup(id, {
				name: editName.trim(),
				base_callsigns: editCallsigns
					.split(/[,\s]+/)
					.map((s) => s.trim())
					.filter(Boolean),
				expected_balloon_count: editCount ?? undefined,
			});
			cancelEdit();
			await refresh();
		} catch (err) {
			alert(`Error: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			savingEdit = false;
		}
	}

	async function start(id: number) {
		await api.start(id);
		await refresh();
	}

	async function stop(id: number) {
		await api.stop(id);
		await refresh();
	}

	async function reset(id: number) {
		if (!confirm('Reset all stats for this launch group? This cannot be undone.')) return;
		await api.reset(id);
		await refresh();
	}

	async function remove(id: number) {
		if (!confirm('Delete this launch group and all its data?')) return;
		await api.deleteGroup(id);
		await refresh();
	}

	let busyPayload = $state<string | null>(null);

	async function markLaunched(groupId: number, callsign: string) {
		busyPayload = `${groupId}:${callsign}`;
		try {
			await api.markLaunched(groupId, callsign);
			await refresh();
		} catch (err) {
			alert(`Error: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			busyPayload = null;
		}
	}

	async function toggleRecovered(groupId: number, callsign: string) {
		busyPayload = `${groupId}:${callsign}`;
		try {
			await api.toggleRecovered(groupId, callsign);
			await refresh();
		} catch (err) {
			alert(`Error: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			busyPayload = null;
		}
	}

	function phaseLabel(p: Payload): string {
		if (p.recovered) return 'recovered';
		if (p.launched_at) return p.phase;
		return 'pre-launch';
	}
</script>

<div class="flex items-center justify-between mb-6 gap-3 flex-wrap">
	<h1 class="text-2xl font-bold">Admin</h1>
	{#if auth.password}
		<button
			onclick={logout}
			class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
		>
			Log out
		</button>
	{/if}
</div>

{#if !auth.password}
	<section class="max-w-sm p-5 rounded border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
		<h2 class="text-lg font-semibold mb-3">Admin password required</h2>
		<form onsubmit={login} class="space-y-3">
			<label class="block">
				<span class="text-sm text-slate-600 dark:text-slate-400">Password</span>
				<input
					bind:value={pwInput}
					type="password"
					required
					autocomplete="current-password"
					class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2"
				/>
			</label>
			{#if loginError}
				<p class="text-sm text-red-500">{loginError}</p>
			{/if}
			<button
				type="submit"
				disabled={loggingIn}
				class="w-full rounded bg-blue-600 hover:bg-blue-700 disabled:bg-slate-400 text-white px-4 py-2 font-medium"
			>
				{loggingIn ? 'Signing in…' : 'Sign in'}
			</button>
		</form>
	</section>
{:else}
<section class="mb-8 p-5 rounded border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
	<h2 class="text-lg font-semibold mb-3">Create Launch Group</h2>
	<form onsubmit={createGroup} class="grid gap-3 sm:grid-cols-2">
		<label class="block">
			<span class="text-sm text-slate-600 dark:text-slate-400">Name</span>
			<input
				bind:value={newName}
				type="text"
				required
				placeholder="Spring 2026 Launch"
				class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2"
			/>
		</label>
		<label class="block">
			<span class="text-sm text-slate-600 dark:text-slate-400">Expected balloons</span>
			<input
				bind:value={newCount}
				type="number"
				min="1"
				placeholder="3"
				class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2"
			/>
		</label>
		<label class="block sm:col-span-2">
			<span class="text-sm text-slate-600 dark:text-slate-400">
				Base callsigns (comma or space separated — SSIDs auto-tracked)
			</span>
			<input
				bind:value={newCallsigns}
				type="text"
				required
				placeholder="KD8CJT, KF8ABL"
				class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono"
			/>
		</label>
		<button
			type="submit"
			disabled={creating}
			class="sm:col-span-2 rounded bg-blue-600 hover:bg-blue-700 disabled:bg-slate-400 text-white px-4 py-2 font-medium"
		>
			{creating ? 'Creating…' : 'Create'}
		</button>
	</form>
</section>

<section>
	<h2 class="text-lg font-semibold mb-3">Launch Groups</h2>
	{#if loading}
		<p class="text-slate-500">Loading…</p>
	{:else if error}
		<p class="text-red-500">Error: {error}</p>
	{:else if groups.length === 0}
		<p class="text-slate-500">No launch groups yet.</p>
	{:else}
		<div class="space-y-3">
			{#each groups as g (g.id)}
				<div class="p-4 rounded border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
					{#if editingId === g.id}
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="block">
								<span class="text-sm text-slate-600 dark:text-slate-400">Name</span>
								<input
									bind:value={editName}
									type="text"
									required
									class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2"
								/>
							</label>
							<label class="block">
								<span class="text-sm text-slate-600 dark:text-slate-400">Expected balloons</span>
								<input
									bind:value={editCount}
									type="number"
									min="1"
									class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2"
								/>
							</label>
							<label class="block sm:col-span-2">
								<span class="text-sm text-slate-600 dark:text-slate-400">
									Base callsigns (comma or space separated)
								</span>
								<input
									bind:value={editCallsigns}
									type="text"
									required
									class="mt-1 w-full rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono"
								/>
							</label>
							<div class="sm:col-span-2 flex gap-2 justify-end">
								<button
									onclick={cancelEdit}
									disabled={savingEdit}
									class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
								>
									Cancel
								</button>
								<button
									onclick={() => saveEdit(g.id)}
									disabled={savingEdit}
									class="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 disabled:bg-slate-400 text-white text-sm"
								>
									{savingEdit ? 'Saving…' : 'Save'}
								</button>
							</div>
						</div>
					{:else}
						<div class="flex items-start justify-between gap-3 flex-wrap">
							<div>
								<div class="flex items-center gap-2">
									<h3 class="font-semibold">{g.name}</h3>
									{#if g.active}
										<span class="text-xs px-2 py-0.5 rounded bg-green-600 text-white">ACTIVE</span>
									{:else}
										<span class="text-xs px-2 py-0.5 rounded bg-slate-500 text-white">inactive</span>
									{/if}
								</div>
								<div class="text-xs text-slate-500 mt-1 font-mono">{g.base_callsigns.join(', ')}</div>
								<div class="text-xs text-slate-500 mt-1">
									Expected: {g.expected_balloon_count ?? '—'} ·
									Created: {formatDateTime(g.created_at)}
									{#if g.started_at}· Started {formatAge(g.started_at, clock.now)}{/if}
								</div>
							</div>
							<div class="flex gap-2 flex-wrap">
								<a
									href="/launch/{g.id}"
									class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
								>
									Dashboard
								</a>
								<button
									onclick={() => startEdit(g)}
									class="px-3 py-1 rounded border border-slate-300 dark:border-slate-600 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
								>
									Edit
								</button>
								{#if g.active}
									<button
										onclick={() => stop(g.id)}
										class="px-3 py-1 rounded bg-orange-600 hover:bg-orange-700 text-white text-sm"
									>
										Stop
									</button>
								{:else}
									<button
										onclick={() => start(g.id)}
										class="px-3 py-1 rounded bg-green-600 hover:bg-green-700 text-white text-sm"
									>
										Start
									</button>
								{/if}
								<button
									onclick={() => reset(g.id)}
									class="px-3 py-1 rounded border border-orange-500 text-orange-600 dark:text-orange-400 text-sm hover:bg-orange-50 dark:hover:bg-orange-950"
								>
									Reset stats
								</button>
								<button
									onclick={() => remove(g.id)}
									class="px-3 py-1 rounded border border-red-500 text-red-600 dark:text-red-400 text-sm hover:bg-red-50 dark:hover:bg-red-950"
								>
									Delete
								</button>
							</div>
						</div>

						{#if g.payloads.length > 0}
							<div class="mt-4 border-t border-slate-200 dark:border-slate-800 pt-3">
								<div class="text-xs font-semibold uppercase text-slate-500 mb-2">Payloads</div>
								<div class="space-y-2">
									{#each g.payloads as p (p.callsign)}
										{@const busy = busyPayload === `${g.id}:${p.callsign}`}
										<div
											class="flex items-center justify-between gap-2 flex-wrap text-sm border border-slate-200 dark:border-slate-800 rounded px-3 py-2"
										>
											<div class="flex items-center gap-2 flex-wrap">
												<span class="font-mono font-semibold">{p.callsign}</span>
												<span
													class="text-[10px] uppercase px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300"
												>
													{p.type}
												</span>
												<span class="text-xs text-slate-500">{phaseLabel(p)}</span>
												{#if p.last_heard}
													<span class="text-xs text-slate-400">
														· heard {formatAge(p.last_heard, clock.now)}
													</span>
												{/if}
											</div>
											<div class="flex gap-2">
												{#if p.type === 'balloon' || p.type === 'unknown'}
													{#if !p.launched_at}
														<button
															onclick={() => markLaunched(g.id, p.callsign)}
															disabled={busy}
															class="px-2 py-1 rounded border border-green-500 text-green-600 dark:text-green-400 text-xs hover:bg-green-50 dark:hover:bg-green-950 disabled:opacity-50"
														>
															Mark launched
														</button>
													{:else}
														<span class="text-xs text-slate-500">
															launched {formatAge(p.launched_at, clock.now)}
														</span>
													{/if}
													<button
														onclick={() => toggleRecovered(g.id, p.callsign)}
														disabled={busy}
														class="px-2 py-1 rounded border border-blue-500 text-blue-600 dark:text-blue-400 text-xs hover:bg-blue-50 dark:hover:bg-blue-950 disabled:opacity-50"
													>
														{p.recovered ? 'Un-recover' : 'Mark recovered'}
													</button>
												{/if}
											</div>
										</div>
									{/each}
								</div>
							</div>
						{:else if g.active}
							<p class="mt-3 text-xs text-slate-500 border-t border-slate-200 dark:border-slate-800 pt-3">
								No payloads heard yet. They will appear here once the cron receives telemetry.
							</p>
						{/if}
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</section>
{/if}
