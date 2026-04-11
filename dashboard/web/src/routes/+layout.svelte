<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import Header from '$lib/components/Header.svelte';

	let { children } = $props();

	onMount(() => {
		// Apply stored theme
		const stored = localStorage.getItem('theme');
		const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
		if (stored === 'dark' || (!stored && prefersDark)) {
			document.documentElement.classList.add('dark');
		}
	});
</script>

<svelte:head>
	<title>UMich Balloons</title>
	<meta name="viewport" content="width=device-width, initial-scale=1" />
</svelte:head>

<div class="min-h-screen flex flex-col">
	<Header />
	<main class="flex-1 container mx-auto px-4 py-6 max-w-7xl">
		{@render children()}
	</main>
	<footer class="border-t border-slate-200 dark:border-slate-800 py-4 text-center text-sm text-slate-500">
		UMich Balloons · <a href="https://github.com/umich-balloons" class="hover:underline">GitHub</a>
	</footer>
</div>
