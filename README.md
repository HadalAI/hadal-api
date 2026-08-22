# Hadal Research Backend API

FastAPI server for the Hadal Research platform.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Endpoints

- `GET /` - Health check
- `GET /models` - List models
- `POST /research-runs` - Create research run
- `GET /research-runs` - List research runs
- `POST /contributors` - Register contributor
- `POST /workers/register` - Register worker

## Deployment

```bash
docker build -t hadal-api .
docker run -p 8000:8000 hadal-api
```
