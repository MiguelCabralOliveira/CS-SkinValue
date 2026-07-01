# Deploy guide — CS-SkinValue

Full-stack app: **FastAPI** (Python) + **SvelteKit** (TypeScript) in a single container, deployed to **Railway**.

## Local dev (two terminals)

```bash
# Terminal 1 — backend
.venv/bin/python -m uvicorn app:app --reload --port 8000

# Terminal 2 — frontend (hot reload, hits backend on :8000)
cd frontend && npm run dev
# → http://localhost:5173
```

## Local prod build (single port, mirrors Railway)

```bash
cd frontend && npm run build && cd ..
.venv/bin/python app.py
# → http://localhost:8000
```

## Railway deploy

1. **Install CLI** (one time): `brew install railway` or `npm i -g @railway/cli`
2. **Login**: `railway login`
3. **Init project** (one time):
   ```bash
   railway init   # pick "Empty Project", give it a name
   railway link   # links current dir
   ```
4. **Deploy**:
   ```bash
   railway up
   ```
   Railway picks up `railway.toml`, builds with the `Dockerfile`, and serves on a `*.railway.app` URL.

5. **Add a custom domain** (optional): in Railway dashboard → service settings → networking → custom domain.

## Image size

The Dockerfile bakes `data/items.parquet` (~745MB) into the image. Final image: ~1.5GB.

**To slim down (recommended for production)**: move parquets to a Railway volume:

```bash
railway volume create skinvalue-data
railway volume attach skinvalue-data /app/data
# Then upload parquets to that volume via railway shell.
```

And drop the `COPY data/*.parquet` lines from the Dockerfile.

## Resource tuning

- **RAM**: Chronos-T5-small needs ~600MB. Add the items.parquet load (~300MB resident polars) → total ~1GB working set.
  - Railway default 512MB → **bump to 1GB** in service settings.
- **CPU**: 0.5 vCPU is enough; first request takes ~3s (cold model warmup), subsequent ~1s.

## Environment variables

None required for default behavior. Optional:

| Var | Default | Effect |
|---|---|---|
| `PORT` | `8000` | Port FastAPI binds to (Railway sets automatically) |
| `CHRONOS_MODEL` | `chronos` | (future) default model name |

## Routes

| Path | What |
|---|---|
| `GET /` | Svelte SPA (item picker, plot, table) |
| `GET /api/health` | Service status + available models |
| `GET /api/items` | List of modelable items, filter by tier |
| `GET /api/items/search?q=` | Substring search |
| `GET /api/forecast?name=&horizon=&model=` | Single forecast |
| `POST /api/forecast/batch` | Many items at once |
| `GET /docs` | Interactive Swagger UI |
