<script lang="ts">
	import { onMount } from 'svelte';
	import type { ForecastResponse } from '$lib/api';

	let { forecast }: { forecast: ForecastResponse } = $props();

	let el: HTMLDivElement;
	// plotly.js is browser-only; import lazily so SSR/build doesn't choke.
	let Plotly: typeof import('plotly.js-dist-min') | null = null;

	onMount(async () => {
		Plotly = (await import('plotly.js-dist-min')).default;
		render();
	});

	$effect(() => {
		// re-render whenever a new forecast arrives (and Plotly is ready)
		forecast;
		if (Plotly) render();
	});

	function render() {
		if (!Plotly || !el) return;

		const dates = forecast.points.map((p) => p.date);
		const point = forecast.points.map((p) => p.point);
		const lower = forecast.points.map((p) => p.lower);
		const upper = forecast.points.map((p) => p.upper);

		// Anchor the forecast to the last observed price so the line is continuous.
		const anchorDates = [forecast.anchor_date, ...dates];
		const anchorPoint = [forecast.anchor_price, ...point];

		// Observed history leading up to the anchor.
		const histDates = forecast.history.map((h) => h.date);
		const histPrices = forecast.history.map((h) => h.price);

		const history = {
			x: histDates,
			y: histPrices,
			mode: 'lines',
			line: { color: '#8b91a3', width: 1.5 },
			name: 'History',
			type: 'scatter'
		};

		const band = {
			x: [...dates, ...dates.slice().reverse()],
			y: [...upper, ...lower.slice().reverse()],
			fill: 'toself',
			fillcolor: 'rgba(79, 152, 255, 0.15)',
			line: { color: 'transparent' },
			name: '80% band',
			hoverinfo: 'skip',
			type: 'scatter'
		};

		const line = {
			x: anchorDates,
			y: anchorPoint,
			mode: 'lines+markers',
			line: { color: '#4f98ff', width: 2 },
			marker: { size: 5 },
			name: 'Forecast',
			type: 'scatter'
		};

		const anchor = {
			x: [forecast.anchor_date],
			y: [forecast.anchor_price],
			mode: 'markers',
			marker: { size: 9, color: '#facc15', symbol: 'circle' },
			name: 'Current',
			type: 'scatter'
		};

		const layout = {
			margin: { l: 55, r: 20, t: 20, b: 40 },
			height: 340,
			paper_bgcolor: 'transparent',
			plot_bgcolor: 'transparent',
			font: { color: '#8b91a3', size: 12 },
			xaxis: { gridcolor: '#1f2230', zeroline: false },
			yaxis: { gridcolor: '#1f2230', zeroline: false, tickprefix: '$' },
			legend: { orientation: 'h', y: 1.12, x: 0 },
			showlegend: true
		};

		Plotly.react(el, [history, band, line, anchor] as never, layout as never, {
			responsive: true,
			displayModeBar: false
		});
	}
</script>

<div bind:this={el} class="chart"></div>

<style>
	.chart {
		width: 100%;
		min-height: 340px;
	}
</style>
