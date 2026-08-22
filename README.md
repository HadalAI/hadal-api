# Hadal Research Backend API

FastAPI + SQLite server for the Hadal Research platform. All public stats are
computed from real worker activity — nothing is hardcoded.

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Data persists in `hadal.db` (SQLite, created on first start). Set `HADAL_DB`
to change its path.

## Endpoints

- `GET /` - Health check
- `GET /stats` - Live network stats (contributors, gpu_hours, workers_online, active_runs)
- `POST /research-runs` - Create research run (admin; send `X-Admin-Token` header when `HADAL_ADMIN_TOKEN` is set)
- `GET /research-runs` - List research runs
- `GET /research-runs/{slug}` - Get one run
- `POST /workers/register` - Register a worker `{id, gpu, vram}`
- `POST /workers/heartbeat` - Worker heartbeat `{id}` — credits elapsed time (capped at 10 min per gap) as GPU hours
- `GET /models` - List model releases

Environment: `HADAL_DB` (db path), `HADAL_ADMIN_TOKEN` (enables admin auth on writes).

## Deployment

```bash
docker build -t hadal-api .
docker run -p 8000:8000 -v hadal-data:/data -e HADAL_DB=/data/hadal.db hadal-api
```
