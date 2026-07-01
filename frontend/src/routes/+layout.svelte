<script lang="ts">
	let { children } = $props();
</script>

<svelte:head>
	<title>CS-SkinValue · Forecasts</title>
	<meta name="viewport" content="width=device-width, initial-scale=1" />
</svelte:head>

<main>
	{@render children()}
</main>

<style>
	:global(body) {
		margin: 0;
		font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
		background: #0e0f13;
		color: #e6e8eb;
	}
	:global(*) {
		box-sizing: border-box;
	}
	main {
		max-width: 1100px;
		margin: 0 auto;
		padding: 2rem 1.25rem 4rem;
	}
</style>
