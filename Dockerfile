# ---- Stage 1: build SvelteKit frontend ----------------------------------
FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---- Stage 2: Python runtime --------------------------------------------
FROM python:3.12-slim AS runtime

# System deps for numpy/pyarrow/torch wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cacheable layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt fastapi uvicorn[standard] \
 && pip install --no-cache-dir chronos-forecasting

# Copy application code
COPY ETL/ ./ETL/
COPY forecasting/ ./forecasting/
COPY app.py ./

# Copy built frontend from stage 1
COPY --from=frontend-build /frontend/build ./frontend/build

# Data parquets are mounted at runtime (Railway volume) OR baked in.
# For simplicity, bake them in — items.parquet is ~745MB so this makes the
# image fat (~1.5GB). If you want to slim down, move to Railway volume.
COPY data/items.parquet ./data/items.parquet
COPY data/whitelist_tiers.parquet ./data/whitelist_tiers.parquet

# Chronos pre-download (saves cold-start time)
RUN python -c "from chronos import ChronosPipeline; ChronosPipeline.from_pretrained('amazon/chronos-t5-small')"

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
