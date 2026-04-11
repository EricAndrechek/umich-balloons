<script lang="ts">
	let { phase }: { phase: string } = $props();

	const styles: Record<string, string> = {
		'pre-launch': 'bg-slate-500 text-white',
		ascending: 'bg-blue-600 text-white',
		descending: 'bg-orange-600 text-white',
		landed: 'bg-green-700 text-white',
	};
</script>

<span class="inline-block text-xs font-semibold px-2 py-0.5 rounded {styles[phase] ?? 'bg-slate-500 text-white'}">
	{phase}
</span>
