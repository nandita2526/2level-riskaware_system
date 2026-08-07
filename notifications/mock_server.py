"""
Mock notification receiver — simulates the "push notifications to security
personnel" endpoint described in Section IV of the paper. Run this alongside
main.py during your demo to show critical alerts arriving with full incident
metadata (risk score, timestamp, camera id, cropped snapshot).

Usage:
    python notifications/mock_server.py
    # then, in another terminal:
    python main.py --source 0
"""
import base64
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify

app = Flask(__name__)

SNAPSHOT_DIR = Path(__file__).resolve().parent / "received_snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)


@app.route("/alert", methods=["POST"])
def receive_alert():
    payload = request.get_json(force=True)

    snapshot_b64 = payload.pop("snapshot_base64", None)
    saved_path = None
    if snapshot_b64:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        saved_path = SNAPSHOT_DIR / f"track{payload.get('track_id')}_{ts}.jpg"
        with open(saved_path, "wb") as f:
            f.write(base64.b64decode(snapshot_b64))

    print("=" * 60)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] CRITICAL ALERT RECEIVED")
    for key, value in payload.items():
        print(f"  {key}: {value}")
    if saved_path:
        print(f"  snapshot saved to: {saved_path}")
    print("=" * 60)

    return jsonify({"status": "received", "saved_snapshot": str(saved_path) if saved_path else None}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    print("Mock security-personnel notification server running on http://127.0.0.1:5000")
    print("Waiting for critical alerts at POST /alert ...")
    app.run(host="127.0.0.1", port=5000, debug=False)
