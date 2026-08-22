"""Hadal Research API — SQLite-backed, honest numbers only."""
import os
import sqlite3
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

DB = os.environ.get("HADAL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hadal.db"))
ADMIN_TOKEN = os.environ.get("HADAL_ADMIN_TOKEN", "")

app = FastAPI(title="Hadal Research API")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PLANNED',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workers(
  id TEXT PRIMARY KEY,
  gpu TEXT NOT NULL DEFAULT 'CPU',
  vram REAL NOT NULL DEFAULT 0,
  registered_at REAL NOT NULL,
  last_seen REAL,
  gpu_hours REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS models(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PLANNED',
  created_at REAL NOT NULL
);
"""

_conn = sqlite3.connect(DB)
_conn.executescript(SCHEMA)
_conn.commit()
_conn.close()


def q(sql, params=(), write=False):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        if write:
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
    return {"name": "Hadal Research API", "version": "0.1.0"}


@app.post("/research-runs", status_code=201)
def create_run(run: RunIn, x_admin_token: str = Header(default="")):
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="admin token required")
    q(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
        (run.slug, run.name, run.description, run.status, time.time()),
        write=True,
    )
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
    q(
        "INSERT OR REPLACE INTO workers VALUES (?,?,?,?,NULL,0)",
        (w.id, w.gpu, w.vram, time.time()),
        write=True,
    )
    return {"status": "registered", "id": w.id}


@app.post("/workers/heartbeat")
def heartbeat(hb: Heartbeat):
    rows = q("SELECT last_seen FROM workers WHERE id=?", (hb.id,))
    if not rows:
        raise HTTPException(status_code=404, detail="unknown worker - register first")
    last = rows[0]["last_seen"]
    # ponytail: credit elapsed-since-last-heartbeat, capped at 10min; per-job accounting when jobs exist
    delta = min(max(time.time() - last, 0), 600) if last else 0
    q(
        "UPDATE workers SET last_seen=?, gpu_hours=gpu_hours+? WHERE id=?",
        (time.time(), delta / 3600.0, hb.id),
        write=True,
    )
    return {"status": "alive"}


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
