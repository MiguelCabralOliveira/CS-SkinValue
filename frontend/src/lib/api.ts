// Typed client for the CS-SkinValue FastAPI backend.
//
// In production the SPA is served from the same origin as the API, so a
// relative base ("") works. In dev (`vite dev` on :5173) we point at the
// backend on :8000 — CORS is wide-open server-side, so no proxy is needed.
const BASE = import.meta.env.DEV ? 'http://localhost:8000' : '';

export interface Item {
	name: string;
	weapon: string | null;
	type: string | null;
	tier: number | null;
	price: number | null;
}

export interface Health {
	status: string;
	models: string[];
	items_loaded: number;
}

export interface ForecastPoint {
	date: string;
	point: number;
	lower: number;
	upper: number;
}

export interface HistoryPoint {
	date: string;
	price: number;
}

export interface Backtest {
	mape_h1: number;
	mape_h7: number;
	n_folds: number;
}

export interface ForecastResponse {
	name: string;
	model: string;
	horizon: number;
	anchor_price: number;
	anchor_date: string;
	history: HistoryPoint[];
	points: ForecastPoint[];
	change_pct_7d: number;
	direction: string;
	backtest: Backtest | null;
}

export interface MarketQuote {
	market: string;
	buy_price: number;
	volume: number | null;
	sell_fee: number;
	net_sell: number;
}

export interface ArbitrageResponse {
	name: string;
	steam_price: number | null;
	cheapest_buy_market: string;
	cheapest_buy_price: number;
	best_sell_market: string;
	best_sell_net: number;
	spread_abs: number;
	spread_pct: number;
	quotes: MarketQuote[];
}

async function getJSON<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE}${path}`);
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body?.detail ?? detail;
		} catch {
			/* non-JSON error body */
		}
		throw new Error(detail);
	}
	return res.json() as Promise<T>;
}

export const api = {
	health: () => getJSON<Health>('/api/health'),

	items: (tier?: number, limit = 500) => {
		const p = new URLSearchParams({ limit: String(limit) });
		if (tier != null) p.set('tier', String(tier));
		return getJSON<Item[]>(`/api/items?${p}`);
	},

	searchItems: (q: string, limit = 20) =>
		getJSON<Item[]>(`/api/items/search?q=${encodeURIComponent(q)}&limit=${limit}`),

	forecast: (name: string, horizon = 7, model = 'chronos') =>
		getJSON<ForecastResponse>(
			`/api/forecast?name=${encodeURIComponent(name)}&horizon=${horizon}&model=${encodeURIComponent(model)}`
		),

	arbitrage: (name: string) =>
		getJSON<ArbitrageResponse>(`/api/arbitrage?name=${encodeURIComponent(name)}`)
};
