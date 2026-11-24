"""Smoke test every HTTP endpoint against a running backend."""

import os
import sys

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        response = client.post(
            "/events",
            data={"type": "unknown_visitor", "camera": "front_door"},
            files={"snapshot": ("test.png", b"\x89PNG\r\n\x1a\nminimal-test-image", "image/png")},
        )
        response.raise_for_status()
        created = response.json()
        event_id = created["id"]
        assert created["status"] == "pending" and created["decision"] is None
        assert created["type"] == "unknown_visitor"

        listing = client.get("/events")
        listing.raise_for_status()
        assert any(item["id"] == event_id for item in listing.json())

        one = client.get(f"/events/{event_id}")
        one.raise_for_status()
        assert one.json()["id"] == event_id

        saved = client.post(f"/events/{event_id}/decision", json={"decision": "accepted"})
        saved.raise_for_status()
        assert saved.json()["status"] == "resolved"

        decision = client.get(f"/events/{event_id}/decision")
        decision.raise_for_status()
        assert decision.json()["decision"] == "accepted"

        snapshot = client.get(created["snapshot_path"])
        snapshot.raise_for_status()
        assert snapshot.content.startswith(b"\x89PNG")
        print(f"All endpoint checks passed for event {event_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Endpoint smoke test failed: {exc}", file=sys.stderr)
        raise
