import os
import pickle
import time
from datetime import datetime

import cv2
import numpy as np

from face_recognition.face_detector import FaceDetector
from face_recognition.face_embedding import FaceEmbedder

from notifications.notification import (
    send_visitor_photo,
    check_user_decision,
    send_access_result,
)


# ==========================================
# CONFIGURATION
# ==========================================

EMBEDDINGS_FILE = "known_faces/embeddings.pkl"
VISITOR_FOLDER = "captured_visitors"

THRESHOLD = 0.8

# Alert only after this many consecutive "unknown" checks (stops 1-frame glitches)
UNKNOWN_FRAMES_REQUIRED = 8

# Run face detection/recognition on every Nth camera frame (keeps video smooth)
PROCESS_EVERY_N_FRAMES = 2

# Minimum seconds between attempts to send a new alert
RETRY_COOLDOWN = 5

# Set True to print recognition distances while tuning THRESHOLD
DEBUG = False


# ==========================================
# LOAD DATABASE
# ==========================================

def load_database():
    if not os.path.exists(EMBEDDINGS_FILE):
        print("❌ No registered faces found.")
        print("Run register_face.py first.")
        return None

    with open(EMBEDDINGS_FILE, "rb") as file:
        return pickle.load(file)


# ==========================================
# RECOGNIZE FACE
# ==========================================

def recognize_face(visitor_embedding, database):
    best_name = "UNKNOWN"
    best_distance = float("inf")

    for name, embeddings in database.items():
        for known_embedding in embeddings:
            distance = np.linalg.norm(known_embedding - visitor_embedding)

            if distance < best_distance:
                best_distance = distance
                best_name = name

    if best_distance <= THRESHOLD:
        return best_name, best_distance

    return "UNKNOWN", best_distance


# ==========================================
# SAVE VISITOR IMAGE
# ==========================================

def save_unknown_visitor(frame):
    os.makedirs(VISITOR_FOLDER, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(VISITOR_FOLDER, f"visitor_{timestamp}.jpg")

    cv2.imwrite(filepath, frame)

    print()
    print("🚨 UNKNOWN VISITOR DETECTED")
    print(f"Image saved: {filepath}")

    return filepath


# ==========================================
# DRAWING HELPERS
# ==========================================

GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)


def put_text(frame, text, pos, color, scale=0.7):
    cv2.putText(
        frame, text, pos,
        cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2,
    )


# ==========================================
# MAIN
# ==========================================

def main():
    database = load_database()
    if database is None:
        return

    detector = FaceDetector()
    embedder = FaceEmbedder()

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        print("❌ Could not open camera.")
        return

    # Smaller frames + minimal buffer = faster, less lag
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    print()
    print("================================")
    print(" AI VISITOR ACCESS SYSTEM")
    print("================================")
    print()
    print("Camera started.")
    print("Press Q to exit.")
    print()

    # --------------------------------------
    # State
    # --------------------------------------

    app_waiting = False          # waiting for the owner's decision in the app
    last_update_id = 0           # kept for compatibility with notification.py
    current_decision = None      # None / "ACCEPT" / "REJECT"
    visitor_sent = False         # an alert was already sent for this visit
    unknown_count = 0            # consecutive unknown checks
    last_send_attempt = 0.0
    frame_count = 0
    last_faces = []              # (x, y, w, h, name, distance) from the last check

    while True:

        # ==================================
        # READ CAMERA
        # ==================================

        ret, frame = camera.read()
        if not ret:
            print("❌ Could not read camera.")
            break

        frame_count += 1

        # ==================================
        # CHECK APP DECISION (instant, never blocks)
        # ==================================

        if app_waiting:
            decision, last_update_id = check_user_decision(last_update_id)

            if decision == "ACCEPT":
                current_decision = "ACCEPT"
                app_waiting = False

                print()
                print("================================")
                print("       ✅ ACCESS GRANTED")
                print("================================")
                print()
                print("🔓 Door should now OPEN.")
                send_access_result("ACCEPT")

                # ESP32 unlock command will be added here

            elif decision == "REJECT":
                current_decision = "REJECT"
                app_waiting = False

                print()
                print("================================")
                print("       ❌ ACCESS DENIED")
                print("================================")
                print()
                print("🔒 Door remains LOCKED.")
                send_access_result("REJECT")

        # ==================================
        # FACE DETECTION + RECOGNITION
        # (only every Nth frame, results are reused for drawing)
        # ==================================

        if frame_count % PROCESS_EVERY_N_FRAMES == 0:

            results = []

            for (x, y, w, h) in detector.detect_faces(frame):
                face = frame[y:y + h, x:x + w]
                if face.size == 0:
                    continue

                face = cv2.resize(face, (160, 160))
                visitor_embedding = embedder.get_embedding(face)
                name, distance = recognize_face(visitor_embedding, database)

                if DEBUG:
                    print(f"[debug] best={name} distance={distance:.3f}")

                results.append((x, y, w, h, name, distance))

            last_faces = results

            # ----------------------------------
            # No face: reset after the visitor leaves
            # ----------------------------------

            if len(results) == 0:
                unknown_count = 0

                if not app_waiting:
                    visitor_sent = False
                    current_decision = None

            # ----------------------------------
            # Face(s) present
            # ----------------------------------

            else:
                any_unknown = any(r[4] == "UNKNOWN" for r in results)

                if any_unknown:
                    unknown_count += 1
                else:
                    unknown_count = 0

                ready_to_alert = (
                    any_unknown
                    and unknown_count >= UNKNOWN_FRAMES_REQUIRED
                    and not app_waiting
                    and not visitor_sent
                    and current_decision is None
                    and time.time() - last_send_attempt >= RETRY_COOLDOWN
                )

                if ready_to_alert:
                    last_send_attempt = time.time()

                    unknown_distance = min(
                        r[5] for r in results if r[4] == "UNKNOWN"
                    )
                    print()
                    print(f"UNKNOWN VISITOR | Distance: {unknown_distance:.3f}")

                    # Save the clean camera image (before drawing on it)
                    image_path = save_unknown_visitor(frame)

                    # Sends in a background thread, returns immediately
                    sent = send_visitor_photo(image_path)

                    if sent:
                        print()
                        print("📱 Approval request sent to the app.")
                        print("Waiting for ACCEPT or REJECT...")

                        app_waiting = True
                        visitor_sent = True
                        current_decision = None
                    else:
                        print("❌ Could not send the approval request.")

        # ==================================
        # DRAW (every frame, from the latest results)
        # ==================================

        for (x, y, w, h, name, distance) in last_faces:

            # ---------- KNOWN PERSON ----------
            if name != "UNKNOWN":
                cv2.rectangle(frame, (x, y), (x + w, y + h), GREEN, 2)
                put_text(frame, f"KNOWN: {name}", (x, y - 10), GREEN)
                put_text(frame, "ACCESS GRANTED", (x, y + h + 25), GREEN)

            # ---------- UNKNOWN PERSON ----------
            else:
                cv2.rectangle(frame, (x, y), (x + w, y + h), RED, 2)

                if app_waiting:
                    put_text(frame, "WAITING FOR APPROVAL", (x, y - 10), RED)
                elif current_decision == "ACCEPT":
                    put_text(frame, "ACCESS GRANTED", (x, y - 10), GREEN)
                elif current_decision == "REJECT":
                    put_text(frame, "ACCESS DENIED", (x, y - 10), RED)
                else:
                    put_text(frame, "UNKNOWN", (x, y - 10), RED)

        # ==================================
        # STATUS ON SCREEN
        # ==================================

        if app_waiting:
            put_text(frame, "WAITING FOR APP APPROVAL", (10, 30), YELLOW)
        elif current_decision == "ACCEPT":
            put_text(frame, "ACCESS GRANTED", (10, 30), GREEN, 0.8)
        elif current_decision == "REJECT":
            put_text(frame, "ACCESS DENIED", (10, 30), RED, 0.8)

        # ==================================
        # SHOW CAMERA
        # ==================================

        cv2.imshow("AI Visitor Access System", frame)

        # ==================================
        # KEYBOARD
        # ==================================

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break

    # ==================================
    # CLEANUP
    # ==================================

    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()