import os
import threading
import time

import cv2
import requests
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
CAMERA_NAME = os.getenv("CAMERA_NAME", "living_room")
UPLOAD_FIELD = "snapshot"   # same field name used by the door lock code
COOLDOWN = {"theft": 20, "abnormal_activity": 30}   # seconds between alerts
UPLOAD_TIMEOUT = 30
STATUS_INTERVAL = 3
CAPTURE_POLL_INTERVAL = 0.5

_last_sent = {}
_last_status_at = 0
_last_capture_poll_at = 0
_lock = threading.Lock()


def _post(event_type, image_path):
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                f"{BACKEND_URL}/events",
                data={"type": event_type, "camera": CAMERA_NAME},
                files={UPLOAD_FIELD: f},
                timeout=UPLOAD_TIMEOUT,
            )
        if not r.ok:
            print(f"[backend] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[backend] could not send {event_type}: {e}")


def send_event(event_type, image_path):
    """event_type: "theft" or "abnormal_activity". Returns immediately."""
    now = time.time()
    with _lock:
        if now - _last_sent.get(event_type, 0) < COOLDOWN.get(event_type, 30):
            return False
        _last_sent[event_type] = now
    threading.Thread(target=_post, args=(event_type, image_path), daemon=True).start()
    return True


def _post_status(status, activity):
    try:
        r = requests.post(
            f"{BACKEND_URL}/status",
            json={"status": status, "activity": activity, "camera": CAMERA_NAME},
            timeout=5,
        )
        if not r.ok:
            print(f"[backend status] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[backend status] could not update live status: {e}")


def send_status(status, activity):
    """Publish the latest monitor reading at most once every few seconds."""
    global _last_status_at
    now = time.time()
    with _lock:
        if now - _last_status_at < STATUS_INTERVAL:
            return False
        _last_status_at = now
    threading.Thread(target=_post_status, args=(status, activity), daemon=True).start()
    return True


def capture_if_requested(frame):
    """Upload one camera frame only after a chat request asks for an image."""
    global _last_capture_poll_at
    now = time.time()
    with _lock:
        if now - _last_capture_poll_at < CAPTURE_POLL_INTERVAL:
            return False
        _last_capture_poll_at = now
    try:
        response = requests.get(f"{BACKEND_URL}/camera/capture-request", timeout=2)
        response.raise_for_status()
        if not response.json().get("capture"):
            return False
        print("[camera] capturing frame requested by chat")
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            print("[backend frame] could not encode camera frame")
            return False
        uploaded = requests.post(
            f"{BACKEND_URL}/camera/frame",
            files={"snapshot": ("current.jpg", encoded.tobytes(), "image/jpeg")},
            timeout=5,
        )
        if not uploaded.ok:
            print(f"[backend frame] {uploaded.status_code}: {uploaded.text}")
        else:
            print(f"[camera] frame sent to backend ({len(encoded)} bytes)")
        return uploaded.ok
    except Exception as e:
        print(f"[backend frame] capture request failed: {e}")
        return False
