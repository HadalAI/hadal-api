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
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
SITE_URL = os.environ.get("HADAL_SITE_URL", "https://hadal.run")

app = FastAPI(title="Hadal Research API")
if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

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
  gpu_hours REAL NOT NULL DEFAULT 0,
  owner_id TEXT,
  name TEXT NOT NULL DEFAULT '',
  paused INTEGER NOT NULL DEFAULT 0
)""",
    "models": """CREATE TABLE IF NOT EXISTS models(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PLANNED',
  created_at REAL NOT NULL
)""",
    "users": """CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  avatar_url TEXT NOT NULL DEFAULT '',
  github_id TEXT,
  discord_id TEXT,
  created_at REAL NOT NULL,
  api_key TEXT
)""",
    "sessions": """CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
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
    # column order differs between pg (with owner/name/paused) and sqlite; branch SQL
    if DATABASE_URL:
        sql = """INSERT INTO workers (id,gpu,vram,registered_at,last_seen,gpu_hours,name,paused)
                 VALUES (?,?,?,?,NULL,0,'',0)
                 ON CONFLICT (id) DO UPDATE SET gpu=EXCLUDED.gpu, vram=EXCLUDED.vram"""
    else:
        sql = "INSERT OR REPLACE INTO workers VALUES (?,?,?,?,NULL,0)"
    q(sql, (w.id, w.gpu, w.vram, time.time()))
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


# ---------- AUTH (GitHub + Discord OAuth) ----------
import secrets
import urllib.parse

from fastapi import Cookie, Response


def _upsert_user(provider: str, provider_id: str, username: str, avatar: str) -> dict:
    """Find-or-create a user keyed by provider id; link second provider if new."""
    rows = q(f"SELECT * FROM users WHERE {provider}_id=?", (provider_id,))
    if rows:
        user = rows[0]
        q("UPDATE users SET username=?, avatar_url=? WHERE id=?", (username, avatar, user["id"]))
        return q(f"SELECT * FROM users WHERE {provider}_id=?", (provider_id,))[0]
    uid = secrets.token_hex(8)
    q(
        f"INSERT INTO users VALUES (?,?,?,?,NULL,?)",
        (uid, username, avatar, provider_id, time.time()),
    )
    return q("SELECT * FROM users WHERE id=?", (uid,))[0]


def _new_session(response: Response, user_id: str):
    token = secrets.token_hex(32)
    q("INSERT INTO sessions VALUES (?,?,?)", (token, user_id, time.time()))
    response.set_cookie(
        "hadal_session", token,
        max_age=60 * 60 * 24 * 30, secure=True, httponly=True, samesite="lax",
    )


@app.get("/auth/{provider}/start")
def auth_start(provider: str):
    if provider not in ("github", "discord"):
        raise HTTPException(status_code=404)
    state = secrets.token_hex(16)
    if provider == "github":
        params = {
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": "https://api.hadal.run/auth/github/callback",
            "scope": "read:user",
            "state": state,
        }
        url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)
    else:
        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": "https://api.hadal.run/auth/discord/callback",
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
        url = "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(params)
    resp = Response(status_code=302)
    resp.headers["Location"] = url
    resp.set_cookie("oauth_state", state, max_age=600, secure=True, httponly=True, samesite="lax")
    return resp


def _finish_oauth(provider: str, code: str, response: Response) -> str:
    import httpx

    if provider == "github":
        tok_r = httpx.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        access = tok_r.json().get("access_token")
        if not access:
            raise HTTPException(status_code=400, detail="token exchange failed")
        hdrs = {"Authorization": f"Bearer {access}"}
        u_r = httpx.get("https://api.github.com/user", headers=hdrs)
        u = u_r.json()
        user = _upsert_user("github", str(u["id"]), u.get("login") or "", u.get("avatar_url") or "")
    else:
        data = {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://api.hadal.run/auth/discord/callback",
        }
        tok_r = httpx.post("https://discord.com/api/oauth2/token", data=data)
        access = tok_r.json().get("access_token")
        if not access:
            raise HTTPException(status_code=400, detail="token exchange failed")
        u_r = httpx.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access}"},
        )
        u = u_r.json()
        avatar = (
            f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png"
            if u.get("avatar")
            else ""
        )
        user = _upsert_user("discord", u["id"], u.get("username") or "", avatar)

    _new_session(response, user["id"])
    return user["username"]


@app.get("/auth/github/callback")
def github_callback(code: str, response: Response):
    _finish_oauth("github", code, response)
    return Response(status_code=302, headers={"Location": SITE_URL})


@app.get("/auth/discord/callback")
def discord_callback(code: str, response: Response):
    _finish_oauth("discord", code, response)
    return Response(status_code=302, headers={"Location": SITE_URL})


@app.get("/me")
def me(hadal_session: str = Cookie(default="")):
    if not hadal_session:
        raise HTTPException(status_code=401, detail="not signed in")
    rows = q(
        """SELECT u.id, u.username, u.avatar_url, u.github_id, u.discord_id
           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?""",
        (hadal_session,),
    )
    if not rows:
        raise HTTPException(status_code=401, detail="invalid session")
    return rows[0]


@app.post("/logout")
def logout(response: Response, hadal_session: str = Cookie(default="")):
    if hadal_session:
        q("DELETE FROM sessions WHERE token=?", (hadal_session,))
    response.delete_cookie("hadal_session")
    return {"status": "ok"}


# ---------- WORKER MANAGEMENT (dashboard + TUI) ----------
import uuid


def _require_user(hadal_session: str) -> dict:
    if not hadal_session:
        raise HTTPException(status_code=401, detail="sign in first")
    rows = q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (hadal_session,))
    if not rows:
        raise HTTPException(status_code=401, detail="invalid session")
    return rows[0]


@app.post("/account/key")
def ensure_api_key(response: Response, hadal_session: str = Cookie(default="")):
    """Get-or-create the account's worker API key."""
    user = _require_user(hadal_session)
    if not user["api_key"]:
        key = "hk_" + secrets.token_hex(20)
        q("UPDATE users SET api_key=? WHERE id=?", (key, user["id"]))
        user["api_key"] = key
    response.status_code = 200
    return {"api_key": user["api_key"]}


@app.get("/account/workers")
def my_workers(x_worker_key: str = Header(default=""), hadal_session: str = Cookie(default="")):
    """List workers owned by this account. Accepts session cookie OR worker key."""
    if x_worker_key:
        rows = q(
            "SELECT w.id, w.gpu, w.vram, w.name, w.paused, w.last_seen, w.gpu_hours FROM workers w JOIN users u ON u.id=w.owner_id WHERE u.api_key=?",
            (x_worker_key,),
        )
        return rows
    user = _require_user(hadal_session)
    return q(
        "SELECT id, gpu, vram, name, paused, last_seen, gpu_hours FROM workers WHERE owner_id=?",
        (user["id"],),
    )


class ClaimIn(BaseModel):
    name: str = ""
    machine: str = ""


@app.post("/workers/claim")
def claim_worker(body: ClaimIn, x_worker_key: str = Header(default="")):
    """Called by the TUI on first run: binds this machine to an account via its key.

    Machine identity: hostname+mac hash so re-runs find the same worker row.
    """
    if not x_worker_key:
        raise HTTPException(status_code=401, detail="worker key required")
    owners = q("SELECT id FROM users WHERE api_key=?", (x_worker_key,))
    if not owners:
        raise HTTPException(status_code=401, detail="invalid worker key")
    owner_id = owners[0]["id"]
    machine = body.machine or secrets.token_hex(4)
    wid = f"{machine[:12]}"
    existing = q("SELECT * FROM workers WHERE id=?", (wid,))
    if existing:
        q("UPDATE workers SET name=?, owner_id=? WHERE id=?", (body.name or existing[0]["name"], owner_id, wid))
    else:
        q(
            "INSERT INTO workers VALUES (?,?,?,?,NULL,0,?,?,0)",
            (wid, "detecting", 0, time.time(), owner_id, body.name or ""),
        )
    return {"worker_id": wid, "owner": True}


class SettingsIn(BaseModel):
    name: str | None = None
    paused: bool | None = None


@app.patch("/workers/{worker_id}")
def update_worker(worker_id: str, body: SettingsIn, x_worker_key: str = Header(default="")):
    if not x_worker_key:
        raise HTTPException(status_code=401, detail="worker key required")
    rows = q("SELECT w.* FROM workers w JOIN users u ON u.id=w.owner_id WHERE w.id=? AND u.api_key=?", (worker_id, x_worker_key))
    if not rows:
        raise HTTPException(status_code=404, detail="worker not found for this key")
    if body.name is not None:
        q("UPDATE workers SET name=? WHERE id=?", (body.name, worker_id))
    if body.paused is not None:
        q("UPDATE workers SET paused=? WHERE id=?", (1 if body.paused else 0, worker_id))
    return q("SELECT id, gpu, vram, name, paused, last_seen, gpu_hours FROM workers WHERE id=?", (worker_id,))[0]
