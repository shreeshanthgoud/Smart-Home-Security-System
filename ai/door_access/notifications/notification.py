import os
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
CAMERA_NAME = os.getenv("CAMERA_NAME", "front_door")
UPLOAD_FIELD = "snapshot"     # must match the file field name in backend/main.py
UPLOAD_TIMEOUT = 30           # seconds to wait for the backend to save the event
DECISION_TIMEOUT = 60         # seconds to wait for the owner's answer
POLL_INTERVAL = 1.0           # seconds between decision checks
RETRY_COOLDOWN = 5            # seconds between new requests

_lock = threading.Lock()
_state = {"busy": False, "result": None}
_last_attempt = {"t": 0.0}


def _worker(image_path):
    """Runs in the background: upload the snapshot, then wait for the decision."""
    result = "REJECT"  # fail safe: door stays locked on any problem
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                f"{BACKEND_URL}/events",
                data={"type": "unknown_visitor", "camera": CAMERA_NAME},
                files={UPLOAD_FIELD: f},
                timeout=UPLOAD_TIMEOUT,
            )
        if not r.ok:
            print(f"[backend] {r.status_code}: {r.text}")
        r.raise_for_status()
        event_id = r.json()["id"]
        print(f"[backend] event {event_id} sent, waiting for the app decision")

        deadline = time.time() + DECISION_TIMEOUT
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                resp = requests.get(
                    f"{BACKEND_URL}/events/{event_id}/decision", timeout=5
                )
                decision = resp.json().get("decision")
            except Exception:
                continue
            if decision == "accepted":
                result = "ACCEPT"
                break
            if decision == "rejected":
                result = "REJECT"
                break
        else:
            print("[backend] no answer in time, access denied")
    except Exception as e:
        print(f"[backend] could not complete the request: {e}")

    with _lock:
        _state["result"] = result
        _state["busy"] = False


def send_visitor_photo(image_path):
    """Starts the request in the background and returns immediately."""
    now = time.time()
    with _lock:
        if _state["busy"] or now - _last_attempt["t"] < RETRY_COOLDOWN:
            return False
        _state["busy"] = True
        _state["result"] = None
    _last_attempt["t"] = now
    threading.Thread(target=_worker, args=(image_path,), daemon=True).start()
    return True


def check_user_decision(last_update_id=0):
    """Instant, never blocks. Returns ("ACCEPT" | "REJECT" | None, last_update_id)."""
    with _lock:
        result = _state["result"]
        _state["result"] = None
    return result, last_update_id


def send_access_result(decision):
    """The app already shows the outcome, so this just logs it."""
    print(f"[access] final decision: {decision}")