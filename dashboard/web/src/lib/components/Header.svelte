<script lang="ts">
	import { onMount } from 'svelte';

	let isDark = $state(false);

	onMount(() => {
		isDark = document.documentElement.classList.contains('dark');
	});

	function toggleTheme() {
		isDark = !isDark;
		document.documentElement.classList.toggle('dark', isDark);
		localStorage.setItem('theme', isDark ? 'dark' : 'light');
	}
</script>

<header class="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
	<div class="container mx-auto px-4 py-3 max-w-7xl flex items-center justify-between">
		<a href="/" class="flex items-center gap-2 font-bold text-lg">
			<img src="/logo.png" alt="UMich Balloons" class="h-8 w-8" />
			<span>UMich Balloons</span>
		</a>
		<nav class="flex items-center gap-4 text-sm">
			<a href="/" class="hover:text-blue-600 dark:hover:text-blue-400">Home</a>
			<a href="/admin" class="hover:text-blue-600 dark:hover:text-blue-400">Admin</a>
			<button
				onclick={toggleTheme}
				class="p-2 rounded hover:bg-slate-100 dark:hover:bg-slate-800"
				aria-label="Toggle dark mode"
			>
				{isDark ? '☀️' : '🌙'}
			</button>
		</nav>
	</div>
</header>
