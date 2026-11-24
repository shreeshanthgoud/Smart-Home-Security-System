# Smart Home Monitoring API

## Setup

Use Python 3.10 or newer. Install dependencies from this directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The Firebase Admin SDK uses Application Default Credentials (ADC) and initializes
the project `ai-smart-home-monitoring-app`. No service account key file is read.
Configure ADC in the environment where the server runs (for local development,
`gcloud auth application-default login`; in Google Cloud, use the attached service
account with Firestore access).

## Run

From `/backend`:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000
```

Interactive API documentation is at `http://localhost:8000/docs`.

## Endpoints

- `POST /events`: multipart fields `type`, `camera`, and image file `snapshot`.
- `GET /events?limit=50`: newest events first.
- `GET /events/{id}`: retrieve one event.
- `POST /events/{id}/decision`: JSON `{"decision":"accepted"}` or `{"decision":"rejected"}`.
- `GET /events/{id}/decision`: return the current decision.
- `GET /snapshots/{filename}`: serve a saved snapshot.

Images are stored under `backend/snapshots`; Firestore stores their `/snapshots/...`
path. Configure allowed origins in `main.py` for a restricted production deployment.

## Endpoint smoke test

With the server running and valid ADC configured, run:

```powershell
python test_api.py
```

The script creates an event, verifies listing and retrieval, submits and polls a
decision, and downloads the snapshot.
