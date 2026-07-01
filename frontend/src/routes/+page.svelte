<script lang="ts">
	import { api, type Item, type ForecastResponse } from '$lib/api';
	import ForecastChart from '$lib/ForecastChart.svelte';

	let query = $state('');
	let suggestions: Item[] = $state([]);
	let selected: Item | null = $state(null);
	let horizon = $state(7);
	let model = $state('chronos');
	let availableModels: string[] = $state(['chronos']);
	let forecastResult: ForecastResponse | null = $state(null);
	let loading = $state(false);
	let error: string | null = $state(null);

	let searchTimer: any = null;

	$effect(() => {
		api.health().then((h) => {
			availableModels = h.models;
		}).catch(() => {});
	});

	function onQueryInput() {
		if (searchTimer) clearTimeout(searchTimer);
		const q = query.trim();
		if (q.length < 2) {
			suggestions = [];
			return;
		}
		searchTimer = setTimeout(async () => {
			try {
				suggestions = await api.searchItems(q, 8);
			} catch (e) {
				suggestions = [];
			}
		}, 200);
	}

	function pick(it: Item) {
		selected = it;
		query = it.name;
		suggestions = [];
		forecastResult = null;
		error = null;
	}

	async function runForecast() {
		if (!selected) return;
		loading = true;
		error = null;
		forecastResult = null;
		try {
			forecastResult = await api.forecast(selected.name, horizon, model);
		} catch (e: any) {
			error = e?.message ?? String(e);
		}
		loading = false;
	}

	const directionEmoji = (d: string) => ({ UP: '📈', DOWN: '📉', FLAT: '➡' })[d] ?? '';
</script>

<header>
	<h1>CS-SkinValue · Forecasts</h1>
	<p class="sub">Zero-shot price predictions using Amazon Chronos · 80% confidence band</p>
</header>

<section class="picker">
	<label>
		<span>Item</span>
		<div class="search">
			<input
				type="text"
				bind:value={query}
				oninput={onQueryInput}
				placeholder="Search: AK-47, Karambit, AWP..."
				autocomplete="off"
			/>
			{#if suggestions.length > 0}
				<ul class="suggestions">
					{#each suggestions as it}
						<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
						<li onclick={() => pick(it)}>
							<span class="name">{it.name}</span>
							{#if it.price !== null}
								<span class="price">${it.price.toFixed(2)}</span>
							{/if}
							{#if it.tier !== null}
								<span class="tier tier-{it.tier}">T{it.tier}</span>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</label>

	<label class="small">
		<span>Horizon</span>
		<input type="range" min="1" max="30" bind:value={horizon} />
		<span class="value">{horizon}d</span>
	</label>

	<label class="small">
		<span>Model</span>
		<select bind:value={model}>
			{#each availableModels as m}
				<option value={m}>{m}</option>
			{/each}
		</select>
	</label>

	<button onclick={runForecast} disabled={!selected || loading}>
		{loading ? 'Predicting...' : 'Predict'}
	</button>
</section>

{#if error}
	<div class="error">⚠ {error}</div>
{/if}

{#if forecastResult}
	<section class="result">
		<div class="kpi-row">
			<div class="kpi">
				<span class="label">Current</span>
				<span class="val">${forecastResult.anchor_price.toFixed(2)}</span>
			</div>
			<div class="kpi">
				<span class="label">+{forecastResult.horizon}d</span>
				<span class="val">${forecastResult.points.at(-1)!.point.toFixed(2)}</span>
			</div>
			<div class="kpi">
				<span class="label">Δ%</span>
				<span class="val" class:up={forecastResult.change_pct_7d > 0} class:down={forecastResult.change_pct_7d < 0}>
					{forecastResult.change_pct_7d > 0 ? '+' : ''}{forecastResult.change_pct_7d.toFixed(1)}%
				</span>
			</div>
			<div class="kpi">
				<span class="label">Direction</span>
				<span class="val">{directionEmoji(forecastResult.direction)} {forecastResult.direction}</span>
			</div>
			<div class="kpi">
				<span class="label">Band end</span>
				<span class="val small-val">
					[${forecastResult.points.at(-1)!.lower.toFixed(2)},
					${forecastResult.points.at(-1)!.upper.toFixed(2)}]
				</span>
			</div>
		</div>

		<ForecastChart forecast={forecastResult} />

		<details>
			<summary>Forecast table</summary>
			<table>
				<thead>
					<tr>
						<th>Date</th>
						<th>Point</th>
						<th>Lower (q10)</th>
						<th>Upper (q90)</th>
					</tr>
				</thead>
				<tbody>
					{#each forecastResult.points as p}
						<tr>
							<td>{p.date}</td>
							<td>${p.point.toFixed(2)}</td>
							<td>${p.lower.toFixed(2)}</td>
							<td>${p.upper.toFixed(2)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</details>
	</section>
{:else if !loading}
	<section class="placeholder">
		<p>Search for an item, choose a horizon, and click <strong>Predict</strong>.</p>
		<p class="hint">Tip: Tier 1 items (most liquid) give the most reliable forecasts. Tier 3 items have wider confidence bands.</p>
	</section>
{/if}

<footer>
	<small>
		Model: zero-shot foundation model · MAPE ~7-9% on liquid items · Forecasts are descriptive, not investment advice.
	</small>
</footer>

<style>
	header { margin-bottom: 1.5rem; }
	h1 { margin: 0 0 0.25rem; font-size: 1.75rem; font-weight: 600; }
	.sub { color: #8b91a3; margin: 0; font-size: 0.95rem; }
	.picker {
		display: grid;
		grid-template-columns: 1fr auto auto auto;
		gap: 0.75rem;
		align-items: end;
		margin-bottom: 1.5rem;
		padding: 1.25rem;
		background: #14161e;
		border: 1px solid #1f2230;
		border-radius: 12px;
	}
	@media (max-width: 720px) {
		.picker { grid-template-columns: 1fr; }
	}
	label { display: flex; flex-direction: column; gap: 0.35rem; }
	label span {
		color: #8b91a3;
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	input[type='text'], select {
		background: #0e0f13;
		color: #e6e8eb;
		border: 1px solid #2a2f3e;
		border-radius: 8px;
		padding: 0.55rem 0.7rem;
		font-size: 0.95rem;
		min-width: 200px;
	}
	input[type='text']:focus, select:focus { outline: none; border-color: #4f98ff; }
	label.small input[type='range'] { width: 140px; }
	label.small .value { font-size: 1.1rem; font-weight: 500; color: #e6e8eb; }
	button {
		background: #4f98ff;
		color: white;
		border: none;
		border-radius: 8px;
		padding: 0.65rem 1.25rem;
		font-size: 0.95rem;
		font-weight: 500;
		cursor: pointer;
	}
	button:disabled { opacity: 0.5; cursor: not-allowed; }
	button:hover:not(:disabled) { background: #3a85f5; }
	.search { position: relative; }
	.suggestions {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		background: #14161e;
		border: 1px solid #2a2f3e;
		border-radius: 8px;
		list-style: none;
		margin: 0;
		padding: 0.3rem 0;
		max-height: 320px;
		overflow-y: auto;
		z-index: 10;
	}
	.suggestions li {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.55rem 0.8rem;
		cursor: pointer;
		font-size: 0.92rem;
	}
	.suggestions li:hover { background: #1f2230; }
	.suggestions .name { flex: 1; }
	.suggestions .price { color: #facc15; font-variant-numeric: tabular-nums; }
	.tier { font-size: 0.7rem; font-weight: 600; padding: 2px 6px; border-radius: 4px; }
	.tier-1 { background: #16a34a; color: white; }
	.tier-2 { background: #2563eb; color: white; }
	.tier-3 { background: #475569; color: white; }
	.result {
		background: #14161e;
		border: 1px solid #1f2230;
		border-radius: 12px;
		padding: 1.25rem;
	}
	.kpi-row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 1rem;
		margin-bottom: 1rem;
	}
	.kpi { display: flex; flex-direction: column; gap: 0.2rem; }
	.kpi .label { color: #8b91a3; font-size: 0.72rem; text-transform: uppercase; }
	.kpi .val { font-size: 1.4rem; font-weight: 600; font-variant-numeric: tabular-nums; }
	.kpi .val.up { color: #34d399; }
	.kpi .val.down { color: #f87171; }
	.kpi .small-val { font-size: 0.9rem; color: #8b91a3; }
	.placeholder { text-align: center; padding: 3rem 1rem; color: #8b91a3; }
	.placeholder .hint { font-size: 0.85rem; margin-top: 0.5rem; }
	.error {
		padding: 0.9rem 1rem;
		background: #3a1e1e;
		border: 1px solid #b91c1c;
		border-radius: 8px;
		margin-bottom: 1rem;
		color: #fda4af;
	}
	details { margin-top: 1.5rem; }
	summary { cursor: pointer; color: #8b91a3; font-size: 0.9rem; }
	table {
		width: 100%;
		border-collapse: collapse;
		margin-top: 0.8rem;
		font-size: 0.88rem;
		font-variant-numeric: tabular-nums;
	}
	th, td { padding: 0.5rem 0.7rem; text-align: left; border-bottom: 1px solid #1f2230; }
	th { color: #8b91a3; font-weight: 500; font-size: 0.78rem; }
	footer { margin-top: 2rem; text-align: center; color: #6b7280; }
</style>
