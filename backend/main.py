"""FastAPI service for smart-home monitoring events."""

import base64
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import firebase_admin
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = "ai-smart-home-monitoring-app"
BACKEND_DIR = Path(__file__).resolve().parent
SNAPSHOTS_DIR = BACKEND_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

try:
    firebase_app = firebase_admin.get_app()
except ValueError:
    firebase_app = firebase_admin.initialize_app(options={"projectId": PROJECT_ID})

from firebase_admin import firestore  # noqa: E402

db = firestore.client(app=firebase_app)
events = db.collection("events")
_live_status = {"status": "unknown", "activity": "unknown", "camera": None, "updated_at": None}
_latest_frame_bytes = None
_latest_frame_at = None
_latest_frame_content_type = "image/jpeg"
_latest_frame_lock = threading.Lock()
_capture_requested = threading.Event()
_frame_ready = threading.Event()

EventType = Literal["unknown_visitor", "theft", "abnormal_activity"]
Decision = Literal["accepted", "rejected"]

app = FastAPI(title="Smart Home Monitoring API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DecisionRequest(BaseModel):
    decision: Decision


class ChatRequest(BaseModel):
    message: str


class MonitorStatusRequest(BaseModel):
    status: str
    activity: str
    camera: str


def serialize_event(doc) -> dict:
    data = doc.to_dict() or {}
    timestamp = data.get("timestamp")
    if hasattr(timestamp, "isoformat"):
        timestamp = timestamp.isoformat()
    return {
        "id": doc.id,
        "type": data.get("type"),
        "camera": data.get("camera"),
        "timestamp": timestamp,
        "snapshot_path": data.get("snapshot_path"),
        "status": data.get("status"),
        "decision": data.get("decision"),
    }


@app.post("/events", status_code=201)
async def create_event(
    type: EventType = Form(...),
    camera: str = Form(..., min_length=1),
    snapshot: UploadFile = File(...),
):
    if not snapshot.filename:
        raise HTTPException(status_code=400, detail="Snapshot filename is required")
    suffix = Path(snapshot.filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Unsupported snapshot image type")

    event_id = uuid4().hex
    filename = f"{event_id}{suffix}"
    path = SNAPSHOTS_DIR / filename
    try:
        content = await snapshot.read()
        if not content:
            raise HTTPException(status_code=400, detail="Snapshot image is empty")
        path.write_bytes(content)
    finally:
        await snapshot.close()

    payload = {
        "type": type,
        "camera": camera,
        "timestamp": datetime.now(timezone.utc),
        "snapshot_path": f"/snapshots/{filename}",
        "status": "pending",
        "decision": None,
    }
    try:
        events.document(event_id).set(payload)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail=f"Could not save event: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "id": event_id,
        **payload,
        "timestamp": payload["timestamp"].isoformat(),
    }


@app.get("/events")
def list_events(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    try:
        docs = events.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [serialize_event(doc) for doc in docs]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not list events") from exc


@app.post("/status")
def update_live_status(body: MonitorStatusRequest):
    """Receive the latest activity-monitor reading for live status answers."""
    _live_status.update({
        "status": body.status,
        "activity": body.activity,
        "camera": body.camera,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True, **_live_status}


@app.get("/camera/capture-request")
def get_capture_request():
    requested = _capture_requested.is_set()
    if requested:
        _capture_requested.clear()
        print("[camera] capture requested by chat")
    return {"capture": requested}


@app.get("/status")
def get_live_status():
    updated_at = _live_status.get("updated_at")
    age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds() if updated_at else None
    return {**_live_status, "is_live": age_seconds is not None and age_seconds <= 10}


@app.post("/camera/frame")
async def update_camera_frame(snapshot: UploadFile = File(...)):
    """Keep only the most recent camera frame in memory for on-demand chat."""
    global _latest_frame_bytes, _latest_frame_at, _latest_frame_content_type
    content_type = snapshot.content_type or "image/jpeg"
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Unsupported camera image type")
    content = await snapshot.read()
    await snapshot.close()
    if not content:
        raise HTTPException(status_code=400, detail="Camera image is empty")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Camera image is too large")
    with _latest_frame_lock:
        _latest_frame_bytes = content
        _latest_frame_at = time.time()
        _latest_frame_content_type = content_type
    _frame_ready.set()
    print(f"[camera] captured frame received ({len(content)} bytes)")
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatRequest):
    """Answer using recent event metadata and capture one frame when requested."""
    global _latest_frame_bytes, _latest_frame_at
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Message is too long")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY is not configured on the backend")

    try:
        docs = events.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()
        recent_events = [
            {
                key: event.get(key)
                for key in ("type", "camera", "timestamp", "status", "decision")
            }
            for event in (serialize_event(doc) for doc in docs)
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve monitoring status") from exc

    pending_count = sum(event.get("status") == "pending" for event in recent_events)
    live_status = get_live_status()
    status = (
        f"Live camera status: {live_status['status']}; current activity: {live_status['activity']}; "
        f"camera: {live_status['camera']}."
        if live_status["is_live"]
        else "Live camera status is unavailable or stale. "
    )
    status += " " + ("No pending saved alerts." if pending_count == 0 else f"{pending_count} pending saved alert(s).")
    normalized_message = message.lower()
    image_requested = any(
        phrase in normalized_message
        for phrase in ("image", "current photo", "take a picture", "capture", "camera view", "snapshot", "photo", "picture", "what can you see", "what do you see")
    )
    image_data_url = None
    if image_requested:
        with _latest_frame_lock:
            _latest_frame_bytes = None
            _latest_frame_at = None
        _frame_ready.clear()
        _capture_requested.set()
        if not _frame_ready.wait(timeout=8):
            _capture_requested.clear()
            raise HTTPException(status_code=503, detail="Could not capture a frame. Check that activity monitoring is running.")
        with _latest_frame_lock:
            frame_bytes = _latest_frame_bytes
            frame_content_type = _latest_frame_content_type
        if not frame_bytes:
            raise HTTPException(status_code=503, detail="The activity monitor returned an empty camera frame.")
        encoded = base64.b64encode(frame_bytes).decode("ascii")
        image_data_url = f"data:{frame_content_type};base64,{encoded}"
        if live_status["is_live"]:
            reply = (
                f"Current status: {live_status['status']}. "
                f"Current activity: {live_status['activity']}. "
                f"Camera: {live_status['camera']}."
            )
        else:
            reply = "Camera image captured. Live status is unavailable or stale."
        return {"reply": reply, "image": image_data_url}

    prompt = (
        "You are a concise smart home monitoring assistant. Answer the user's question using only the supplied "
        "status and recent event metadata. Do not claim live camera detection beyond these records. If the data "
        "does not answer a question, say what is unavailable.\n\n"
        f"Current status: {status}\n"
        f"Recent events, newest first: {json.dumps(recent_events, ensure_ascii=False)}\n\n"
        f"User question: {message}"
    )
    model = os.getenv(
        "OPENROUTER_VISION_MODEL" if image_requested else "OPENROUTER_MODEL",
        "openai/gpt-4o-mini" if image_requested else "openrouter/auto",
    )
    user_content = message
    if image_data_url:
        user_content = [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": prompt.rsplit("User question:", 1)[0].strip()},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 500,
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Title": "Smart Home Monitor",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        reply = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not reply:
            raise HTTPException(status_code=502, detail="OpenRouter returned an empty response")
        return {"reply": reply, "image": image_data_url}
    except HTTPException:
        raise
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenRouter request failed with status {exc.code}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Could not get a response from OpenRouter") from exc


@app.get("/events/{event_id}")
def get_event(event_id: str):
    try:
        doc = events.document(event_id).get()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve event") from exc
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    return serialize_event(doc)


@app.post("/events/{event_id}/decision")
def set_decision(event_id: str, body: DecisionRequest):
    ref = events.document(event_id)
    try:
        doc = ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Event not found")
        ref.update({"decision": body.decision, "status": "resolved"})
        return {"id": event_id, "decision": body.decision, "status": "resolved"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not update decision") from exc


@app.get("/events/{event_id}/decision")
def get_decision(event_id: str):
    try:
        doc = events.document(event_id).get()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve decision") from exc
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"id": event_id, "decision": (doc.to_dict() or {}).get("decision")}


@app.get("/snapshots/{filename}")
def get_snapshot(filename: str):
    # Resolve and constrain the path to prevent traversal outside snapshots.
    path = (SNAPSHOTS_DIR / filename).resolve()
    if path.parent != SNAPSHOTS_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(path)
