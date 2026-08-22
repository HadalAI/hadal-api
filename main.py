"""Hadal Research API — honest numbers only. SQLite locally, Neon/Postgres in prod."""
import os
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get("HADAL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hadal.db"))
ADMIN_TOKEN = os.environ.get("HADAL_ADMIN_TOKEN", "")
CORS_ORIGINS = [o for o in os.environ.get("HADAL_CORS_ORIGINS", "").split(",") if o]

app = FastAPI(title="Hadal Research API")
if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

TABLES = {
    "runs": """CREATE TABLE IF NOT EXISTS runs(
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PLANNED',
  created_at REAL NOT NULL
)""",
    "workers": """CREATE TABLE IF NOT EXISTS workers(
  id TEXT PRIMARY KEY,
  gpu TEXT NOT NULL DEFAULT 'CPU',
  vram REAL NOT NULL DEFAULT 0,
  registered_at REAL NOT NULL,
  last_seen REAL,
  gpu_hours REAL NOT NULL DEFAULT 0
)""",
    "models": """CREATE TABLE IF NOT EXISTS models(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PLANNED',
  created_at REAL NOT NULL
)""",
}


def _pg():
    import psycopg  # lazy import so local dev doesn't need it

    return psycopg


def init_db():
    if DATABASE_URL:
        pg = _pg()
        with pg.connect(DATABASE_URL) as conn:
            for ddl in TABLES.values():
                conn.execute(ddl)
            conn.commit()
    else:
        import sqlite3

        conn = sqlite3.connect(SQLITE_PATH)
        for ddl in TABLES.values():
            conn.execute(ddl)
        conn.commit()
        conn.close()


init_db()


def q(sql, params=()):
    """Run a query; returns rows for SELECT, commits otherwise. ? placeholders."""
    if DATABASE_URL:
        pg = _pg()
        rows = []
        with pg.connect(DATABASE_URL) as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            if cur.description:
                cols = [d.name for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.commit()
        return rows
    import sqlite3

    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


class RunIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    status: str = "PLANNED"


class WorkerIn(BaseModel):
    id: str
    gpu: str = "CPU"
    vram: float = 0


class Heartbeat(BaseModel):
    id: str


class ModelIn(BaseModel):
    id: str
    name: str
    description: str = ""
    status: str = "PLANNED"


@app.get("/")
def root():
    return {"name": "Hadal Research API", "version": "0.2.0"}


def upsert_run(run: RunIn):
    if DATABASE_URL:
        sql = """INSERT INTO runs VALUES (?,?,?,?,?)
                 ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name,
                 description=EXCLUDED.description, status=EXCLUDED.status"""
    else:
        sql = "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)"
    q(sql, (run.slug, run.name, run.description, run.status, time.time()))


@app.post("/research-runs", status_code=201)
def create_run(run: RunIn, x_admin_token: str = Header(default="")):
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="admin token required")
    upsert_run(run)
    return q("SELECT * FROM runs WHERE slug=?", (run.slug,))[0]


@app.get("/research-runs")
def list_runs():
    return q("SELECT * FROM runs ORDER BY created_at")


@app.get("/research-runs/{slug}")
def get_run(slug: str):
    rows = q("SELECT * FROM runs WHERE slug=?", (slug,))
    if not rows:
        raise HTTPException(status_code=404, detail="run not found")
    return rows[0]


@app.post("/workers/register")
def register_worker(w: WorkerIn):
    if DATABASE_URL:
        sql = """INSERT INTO workers VALUES (?,?,?,?,NULL,0)
                 ON CONFLICT (id) DO UPDATE SET gpu=EXCLUDED.gpu, vram=EXCLUDED.vram"""
        params = (w.id, w.gpu, w.vram, time.time())
    else:
        sql = "INSERT OR REPLACE INTO workers VALUES (?,?,?,?,NULL,0)"
        params = (w.id, w.gpu, w.vram, time.time())
    q(sql, params)
    return {"status": "registered", "id": w.id}


@app.post("/workers/heartbeat")
def heartbeat(hb: Heartbeat):
    rows = q("SELECT last_seen FROM workers WHERE id=?", (hb.id,))
    if not rows:
        raise HTTPException(status_code=404, detail="unknown worker - register first")
    last = rows[0]["last_seen"]
    # ponytail: credit elapsed-since-last-heartbeat capped at 10min; credit-on-verified-job comes with jobs pipeline
    delta = min(max(time.time() - last, 0), 600) if last else 0
    q(
        "UPDATE workers SET last_seen=?, gpu_hours=gpu_hours+? WHERE id=?",
        (time.time(), delta / 3600.0, hb.id),
    )
    return {"status": "alive"}


@app.get("/workers")
def leaderboard():
    return q("SELECT id, gpu, vram, gpu_hours FROM workers ORDER BY gpu_hours DESC LIMIT 100")


@app.get("/models")
def list_models():
    return q("SELECT * FROM models ORDER BY created_at")


@app.get("/stats")
def stats():
    online_cutoff = time.time() - 300
    return {
        "contributors": q("SELECT COUNT(*) c FROM workers")[0]["c"],
        "workers_online": q("SELECT COUNT(*) c FROM workers WHERE last_seen > ?", (online_cutoff,))[0]["c"],
        "gpu_hours": round(q("SELECT COALESCE(SUM(gpu_hours), 0) s FROM workers")[0]["s"], 2),
        "active_runs": q("SELECT COUNT(*) c FROM runs WHERE status = 'ACTIVE'")[0]["c"],
    }
