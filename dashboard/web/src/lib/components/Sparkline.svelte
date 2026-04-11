<script lang="ts">
	let {
		values,
		width = 120,
		height = 32,
		stroke = 'currentColor',
		fill = 'none',
	}: { values: Array<number | null>; width?: number; height?: number; stroke?: string; fill?: string } = $props();

	const path = $derived(buildPath(values, width, height));

	function buildPath(vs: Array<number | null>, w: number, h: number): string {
		const clean = vs.filter((v): v is number => v != null && !Number.isNaN(v));
		if (clean.length < 2) return '';
		const min = Math.min(...clean);
		const max = Math.max(...clean);
		const range = max - min || 1;
		const step = w / (vs.length - 1);
		let d = '';
		let started = false;
		vs.forEach((v, i) => {
			if (v == null || Number.isNaN(v)) return;
			const x = i * step;
			const y = h - ((v - min) / range) * h;
			d += (started ? 'L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
			started = true;
		});
		return d;
	}
</script>

{#if path}
	<svg {width} {height} viewBox="0 0 {width} {height}" class="inline-block">
		<path d={path} {stroke} stroke-width="1.5" {fill} stroke-linejoin="round" stroke-linecap="round" />
	</svg>
{:else}
	<span class="text-xs text-slate-400">no data</span>
{/if}
