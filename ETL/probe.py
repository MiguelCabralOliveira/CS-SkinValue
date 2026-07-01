import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ETL.config import DATA_DIR, DEFAULT_WORKERS_FALLBACK, PROBE_RESULT_PATH
from ETL.enrich import enrich_item

WORKER_LADDER = [1, 5, 10, 20, 50, 100]
PROBE_SAMPLE_SIZE = 20
MAX_ACCEPTABLE_ERROR_RATE = 0.25
HARD_STOP_ERROR_RATE = 0.50


def _measure(workers: int, sample: list[dict]) -> dict:
    latencies: list[float] = []
    errors = 0
    enriched_count = 0
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for item in sample:
            t0 = time.perf_counter()
            futures.append((t0, ex.submit(enrich_item, item)))
        for t0, fut in futures:
            try:
                result = fut.result()
                latencies.append(time.perf_counter() - t0)
                if result.get("price_series") or result.get("price_ohlc") or result.get("keyfigures"):
                    enriched_count += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
                latencies.append(time.perf_counter() - t0)
    total = len(sample)
    elapsed = time.perf_counter() - start
    error_rate = errors / total if total else 1.0
    return {
        "workers": workers,
        "total": total,
        "errors": errors,
        "error_rate": round(error_rate, 4),
        "enriched": enriched_count,
        "mean_latency_s": round(statistics.mean(latencies), 3) if latencies else None,
        "p95_latency_s": round(statistics.quantiles(latencies, n=20)[18], 3) if len(latencies) >= 20 else None,
        "elapsed_s": round(elapsed, 2),
        "throughput_per_s": round(total / elapsed, 2) if elapsed > 0 else None,
    }


def run_probe(catalog_sample: list[dict]) -> dict:
    sample = catalog_sample[:PROBE_SAMPLE_SIZE]
    results = []
    print(f"Probing concurrency on {len(sample)} sample items...")
    print(f"{'workers':>8} | {'errs':>5} | {'err%':>6} | {'mean(s)':>8} | {'p95(s)':>7} | {'thr/s':>6}")
    for w in WORKER_LADDER:
        m = _measure(w, sample)
        results.append(m)
        print(f"{m['workers']:>8} | {m['errors']:>5} | {m['error_rate']*100:>5.1f}% | "
              f"{str(m['mean_latency_s']):>8} | {str(m['p95_latency_s']):>7} | {str(m['throughput_per_s']):>6}")
        if m["error_rate"] > HARD_STOP_ERROR_RATE:
            print(f"  stopping: error rate {m['error_rate']*100:.1f}% exceeds hard cap")
            break

    eligible = [m for m in results if m["error_rate"] <= MAX_ACCEPTABLE_ERROR_RATE and m["throughput_per_s"]]
    if not eligible:
        chosen = DEFAULT_WORKERS_FALLBACK
        print(f"No worker count met error threshold; falling back to {chosen}")
    else:
        best = max(eligible, key=lambda m: m["throughput_per_s"])
        chosen = best["workers"]
        print(f"Chosen workers: {chosen} (peak throughput {best['throughput_per_s']}/s)")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"chosen_workers": chosen, "ladder": results}
    PROBE_RESULT_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def load_or_run_probe(catalog_sample: list[dict], force: bool = False) -> int:
    if not force and PROBE_RESULT_PATH.exists():
        try:
            data = json.loads(PROBE_RESULT_PATH.read_text())
            return int(data["chosen_workers"])
        except Exception:
            pass
    return int(run_probe(catalog_sample)["chosen_workers"])
