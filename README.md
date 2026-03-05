# Nepal Election Live Results

Monorepo for Nepal election dashboards (2079 + 2082) with:
- FastAPI API
- SQLite (WAL)
- Python poller/ingestion
- Next.js frontend

## Project structure

- `backend/` API + DB + ingestion
- `frontend/` dashboard UI
- `scripts/` local helper scripts
- `deploy/` reverse proxy config

## Local setup (Docker Compose)

1. Copy env file:
   ```bash
   cp .env.example .env
   ```
2. Build and start:
   ```bash
   docker compose up --build
   ```
3. Open:
   - Frontend: `http://localhost:3000/?election=2082` (or `2079`)
   - API: `http://localhost:8000/api/meta?election_id=2082`

## Local setup (without Docker)

### Python version

Use Python `3.11` for local development.

If you are on macOS and only have Python 3.9:
```bash
brew install python@3.11
```

Then recreate the backend venv with Python 3.11:
```bash
cd backend
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
python --version
```

### Backend
```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
python3 -m ingest.discover
python3 -m ingest.poll
```

In another shell:
```bash
cd backend
source .venv/bin/activate
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000/api npm run dev
```

Optional for polygon map:
- Put district boundary GeoJSON at `frontend/public/data/nepal-districts.geojson` (FeatureCollection).
- If missing, UI falls back to a clickable district tile list.

## API endpoints

- `GET /api/meta`
- `GET /api/elections`
- `GET /api/results?election_id=&state_id=&district_id=&constituency_id=&q=&page=&page_size=`
- `GET /api/party?election_id=`
- `GET /api/lookups/states?election_id=`
- `GET /api/lookups/districts?election_id=&state_id=`
- `GET /api/lookups/constituencies?election_id=&district_id=&state_id=`
- `GET /api/analytics/summary?election_id=`
- `GET /api/analytics/timeseries?election_id=&metric=changes|ingest_errors&window=24h`
- `GET /api/analytics/compare?current=2082&previous=2079`

## Freshness semantics

`/api/meta?election_id=2082` exposes:
- `results_updated_at`: last source-change timestamp
- `last_polled_at`: last successful poll timestamp
- `results_updated_at_npt`: Nepal Time display string
- `results_age_seconds`
- `freshness_status`: `fresh`/`stale` (stale > 10 min)

## Election sources

- `backend/data/endpoints_2082.txt`
- `backend/data/endpoints_2079.txt`

Poller reads both by default (`ACTIVE_ELECTIONS=2082,2079`).

## Map data

- `frontend/public/data/nepal-districts.geojson` for district-level map
- `frontend/public/data/nepal-constituencies.geojson` for 165 constituency map

## Hostinger VPS deploy

1. Install Docker + Compose on VPS.
2. Clone repo and create `.env`.
3. Start services:
   ```bash
   docker compose up -d --build
   ```
4. Configure Nginx using `deploy/nginx.conf`.
5. Point domain DNS to VPS and enable Cloudflare proxy.
6. Add Cloudflare cache rule for `/api/*` with 10-30s TTL and rate limiting.

## Election-night ops

- Poller writes snapshots only when content hash changes.
- Snapshot retention:
  - keep all for 24h
  - older snapshots downsampled to 1 per 5 minutes
- Monitor `/api/meta` for stale feed and error counts.
