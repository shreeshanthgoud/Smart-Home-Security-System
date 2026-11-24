# main.py

# 1. Hide the annoying OpenCV [mjpeg overread] spam warnings
import os
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"

import cv2
import time
import threading

# Import from your modules
from model_utils import predict_activity, detect_objects
from backend_client import capture_if_requested, send_event, send_status
from storage_utils import save_snapshot

# Added ?fps=15 to help prevent lag and dropped frames
IP_WEBCAM_URL = 0  # Laptop's default webcam


# ==========================
# LOW LATENCY STREAM
# ==========================
class VideoStream:
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.ret, self.frame = self.cap.read()
        self.running = True

        threading.Thread(target=self.update, daemon=True).start()

    def update(self):
        while self.running:
            if self.cap.isOpened():
                self.cap.grab()
                self.ret, self.frame = self.cap.retrieve()

    def read(self):
        return self.ret, self.frame

    def stop(self):
        self.running = False
        self.cap.release()


# ==========================
# INIT
# ==========================
stream = VideoStream(IP_WEBCAM_URL)

current_status = "normal"
current_activity = "idle"

last_alert_time = 0
latest_frame = None
frame_count = 0

# ==========================
# MAIN LOOP
# ==========================
while True:
    ret, frame = stream.read()

    if not ret:
        print("Reconnecting...")
        stream.stop()
        time.sleep(2)
        stream = VideoStream(IP_WEBCAM_URL)
        continue

    latest_frame = frame.copy()
    frame = cv2.resize(frame, (640, 480))

    frame_count += 1
    capture_if_requested(frame)

    # ==========================
    # COMBINED AI PREDICTION (ACTIVITY + OBJECTS)
    # ==========================
    if frame_count % 6 == 0:

        # 1. Base Activity (Cooking, Cleaning, Theft)
        base_activity = predict_activity(frame)
        base_clean = str(base_activity).strip().lower()
        is_idle = base_clean in ["normal", "idle", "", "none"]

        # 2. YOLO Object Detection
        yolo_status, object_found, boxed_frame = detect_objects(frame)
        print(f"[activity] prediction={base_clean!r}; objects={yolo_status!r}, {object_found!r}")

        # 3. Combine them intelligently into a single phrase
        if yolo_status == "interaction_detected":
            if not is_idle:
                current_activity = f" and interacting with {object_found}"
        else:
            current_activity = base_clean if not is_idle else "idle"

        # 4. Handle Alerts and Notifications
        if base_clean == "theft":
            current_status = "theft"

            # Theft Cooldown (only alert every 15 seconds)
            if time.time() - last_alert_time > 15:
                print("🚨 ALERT: THEFT DETECTED!")
                last_alert_time = time.time()

                path = save_snapshot(frame, "theft_alert")
                send_event("theft", path)

        elif yolo_status == "interaction_detected":
            current_status = "alert"

            # Object Cooldown (only alert every 15 seconds)
            if time.time() - last_alert_time > 15:
                print(f"Alert triggered: Person interacting with {object_found}!")
                last_alert_time = time.time()

                path = save_snapshot(boxed_frame, "auto_alert")
                send_event("abnormal_activity", path)
        else:
            current_status = "normal"

        send_status(current_status, current_activity)
        print(f"[monitor] status={current_status}; activity={current_activity}")


    # ==========================
    # DISPLAY FEED ON SCREEN
    # ==========================
    # Draw the current activity text on the window

    cv2.imshow("Smart Home Monitor", frame)

    if cv2.waitKey(1) & 0xFF == 27: # Press 'Esc' to exit
        break


# ==========================
# CLEANUP
# ==========================
stream.stop()
cv2.destroyAllWindows()
