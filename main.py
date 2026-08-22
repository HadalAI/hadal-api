from fastapi import FastAPI
from pydantic import BaseModel
import uuid

app = FastAPI(title="Hadal Research API")
runs = {}

class Run(BaseModel):
    id: str = ""
    slug: str
    name: str
    description: str
    status: str = "PLANNED"

@app.get("/")
async def root():
    return {"name": "Hadal Research API", "version": "0.0.1"}

@app.post("/research-runs")
async def create_run(run: Run):
    run.id = str(uuid.uuid4())
    runs[run.id] = run
    return run

@app.get("/research-runs")
async def list_runs():
    return list(runs.values())

@app.get("/models")
async def models():
    return [{"id": "hadal-1", "name": "HADAL-1", "contributors": 12847}]
