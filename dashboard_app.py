"""
DASHBOARD APP
================
Flask server that runs the surveillance pipeline in a background thread and
serves the full multi-page operator dashboard:

    /            Live Camera  — live annotated MJPEG feed, per-person risk,
                                 tier cards, Level-1/Level-2 alert console
    /upload      Upload Video — choose a video file, analyze it offline
                                 through the same two-level pipeline
    /history     Alert History — every saved incident (Level-2, AI-verified),
                                  with an Evidence Viewer (snapshot + clip)
    /analytics   Analytics     — totals, AI-verified count, false-positive
                                  rate, average risk, hourly heatmap/timeline

Usage:
    python dashboard_app.py --source 0
    python dashboard_app.py --source rtsp://camera_ip/stream --camera-id CAM_02

Then open http://127.0.0.1:8000 in a browser.
"""
import argparse
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from modules.pipeline import SurveillancePipeline
from modules.module7_evidence_manager import EvidenceManager
from modules import module8_video_upload_analyzer as upload_analyzer

app = Flask(__name__, template_folder="dashboard/templates", static_folder="dashboard/static")

PROJECT_ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = PROJECT_ROOT / "data" / "uploaded_videos"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

# Shared state between the background capture thread and the Flask request handlers
_state = {
    "pipeline": None,
    "latest_jpeg": None,
    "lock": threading.Lock(),
    "running": False,
    "risk_history": [],   # rolling list of {t, max_risk} for the trend chart
    "source": 0,
    "camera_id": "CAM_01",
}

# Shared evidence manager instance so the Live dashboard and the History /
# Analytics pages all read the same evidence_log.csv.
_evidence = EvidenceManager()


def _capture_loop(source, camera_id):
    pipeline = SurveillancePipeline(source=source, camera_id=camera_id)
    _state["pipeline"] = pipeline
    _state["running"] = True

    for display_frame in pipeline.frames():
        if _state.get("_stop_requested"):
            break
        ok, buffer = cv2.imencode(".jpg", display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with _state["lock"]:
                _state["latest_jpeg"] = buffer.tobytes()

        active = pipeline.latest_stats["active_tracks"]
        max_risk = max((t["risk_score"] for t in active.values()), default=0.0)
        _state["risk_history"].append({"t": time.time(), "risk": max_risk})
        if len(_state["risk_history"]) > 120:
            _state["risk_history"] = _state["risk_history"][-120:]

    _state["running"] = False


def _start_capture_thread(source, camera_id):
    _state["_stop_requested"] = False
    _state["source"] = source
    _state["camera_id"] = camera_id
    t = threading.Thread(target=_capture_loop, args=(source, camera_id), daemon=True)
    t.start()
    return t


def _mjpeg_generator():
    placeholder = _blank_frame_jpeg("Waiting for camera feed...")
    while True:
        with _state["lock"]:
            frame = _state["latest_jpeg"] or placeholder
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(1 / 25)


def _blank_frame_jpeg(text: str) -> bytes:
    import numpy as np
    img = np.zeros((480, 640, 3), dtype="uint8")
    cv2.putText(img, text, (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 2)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


# ======================================================================
# Pages
# ======================================================================
@app.route("/")
def index():
    return render_template("index.html", active_page="live")


@app.route("/upload")
def upload_page():
    return render_template("upload.html", active_page="upload")


@app.route("/history")
def history_page():
    return render_template("history.html", active_page="history")


@app.route("/analytics")
def analytics_page():
    return render_template("analytics.html", active_page="analytics")


# ======================================================================
# Live camera
# ======================================================================
@app.route("/video_feed")
def video_feed():
    return Response(_mjpeg_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/stats")
def api_stats():
    pipeline = _state["pipeline"]
    if pipeline is None:
        return jsonify({"running": False})

    stats = pipeline.latest_stats
    uptime = round(time.time() - stats["started_at"], 1)

    return jsonify({
        "running": _state["running"],
        "source": str(_state["source"]),
        "camera_id": _state["camera_id"],
        "fps": stats["fps"],
        "frame_count": stats["frame_count"],
        "uptime_seconds": uptime,
        "active_tracks": stats["active_tracks"],
        "tier_counts": dict(stats["tier_counts"]),
        "recent_alerts": stats["recent_alerts"],
        "recent_warnings": stats["recent_warnings"],
        "risk_history": _state["risk_history"][-60:],
        "model_loaded": pipeline.m4_behaviour.loaded_pretrained,
    })


@app.route("/api/source", methods=["POST"])
def api_set_source():
    """Switches the live feed to a new webcam index / video file / RTSP-IP camera URL."""
    data = request.get_json(force=True, silent=True) or {}
    raw_source = str(data.get("source", "0")).strip()
    camera_id = str(data.get("camera_id") or _state["camera_id"] or "CAM_01")
    if not raw_source:
        return jsonify({"ok": False, "error": "source is required"}), 400

    source = int(raw_source) if raw_source.isdigit() else raw_source

    _state["_stop_requested"] = True
    time.sleep(0.3)  # let the old capture loop exit its generator cleanly
    _start_capture_thread(source, camera_id)
    return jsonify({"ok": True, "source": raw_source, "camera_id": camera_id})


# ======================================================================
# Module 8 — Upload Video Analyzer
# ======================================================================
@app.route("/api/upload", methods=["POST"])
def api_upload_video():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part in request"}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "No file selected"}), 400

    filename = secure_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXT:
        return jsonify({"ok": False, "error": f"Unsupported file type '{ext}'"}), 400

    unique_name = f"{int(time.time())}_{filename}"
    save_path = UPLOADS_DIR / unique_name
    file.save(str(save_path))

    camera_id = request.form.get("camera_id", "UPLOAD")
    job_id = upload_analyzer.start_analysis(str(save_path), camera_id=camera_id)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/upload/<job_id>")
def api_upload_status(job_id):
    job = upload_analyzer.get_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job_id"}), 404
    return jsonify({"ok": True, **job})


# ======================================================================
# Module 7 — Evidence / Alert History
# ======================================================================
@app.route("/api/evidence")
def api_evidence():
    rows = _evidence.read_all()
    rows = list(reversed(rows))  # most recent first

    date_filter = request.args.get("date")
    camera_filter = request.args.get("camera_id")
    if date_filter:
        rows = [r for r in rows if r.get("date") == date_filter]
    if camera_filter:
        rows = [r for r in rows if r.get("camera_id") == camera_filter]

    for r in rows:
        r["image_url"] = f"/evidence/{r['image_path']}" if r.get("image_path") else None
        r["video_url"] = f"/evidence/{r['video_path']}" if r.get("video_path") else None

    return jsonify({"ok": True, "count": len(rows), "incidents": rows})


@app.route("/api/evidence/<int:row_index_from_end>/false_positive", methods=["POST"])
def api_mark_false_positive(row_index_from_end):
    ok = _evidence.mark_false_positive(row_index_from_end)
    return jsonify({"ok": ok})


@app.route("/evidence/<path:filename>")
def serve_evidence(filename):
    """Serves saved incident snapshots/clips from <project_root>/Evidence/..."""
    return send_from_directory(str(PROJECT_ROOT), filename)


# ======================================================================
# Analytics
# ======================================================================
@app.route("/api/analytics")
def api_analytics():
    rows = _evidence.read_all()
    today = datetime.now().strftime("%Y-%m-%d")

    total_alerts = len(rows)
    today_alerts = [r for r in rows if r.get("date") == today]
    ai_verified = [r for r in rows if r.get("level") == "2" or r.get("level") == 2]
    false_positives = [r for r in rows if r.get("false_positive") in ("1", 1, True, "true")]

    fp_rate = (len(false_positives) / total_alerts) if total_alerts else 0.0
    avg_risk = (sum(float(r["risk_score"]) for r in rows if r.get("risk_score")) / total_alerts) \
        if total_alerts else 0.0

    # hourly timeline (last 24h buckets by hour-of-day, across all logged days)
    hourly_counts = defaultdict(int)
    heatmap = defaultdict(lambda: defaultdict(int))  # camera_id -> hour -> count
    for r in rows:
        t = r.get("time", "")
        hour = t.split(":")[0] if ":" in t else "00"
        hourly_counts[hour] += 1
        heatmap[r.get("camera_id", "unknown")][hour] += 1

    timeline = [{"hour": f"{h:02d}", "count": hourly_counts.get(f"{h:02d}", 0)} for h in range(24)]
    heatmap_out = [{"camera_id": cam, "hours": [{"hour": f"{h:02d}", "count": hours.get(f"{h:02d}", 0)}
                                                 for h in range(24)]}
                    for cam, hours in heatmap.items()]

    return jsonify({
        "ok": True,
        "total_alerts_today": len(today_alerts),
        "total_alerts_all_time": total_alerts,
        "total_ai_verified_alerts": len(ai_verified),
        "false_positive_rate": round(fp_rate, 4),
        "false_positive_count": len(false_positives),
        "average_risk": round(avg_risk, 4),
        "timeline": timeline,
        "heatmap": heatmap_out,
    })


# ======================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Risk-Aware Surveillance Dashboard")
    parser.add_argument("--source", default="0", help="Webcam index, video file path, or RTSP/IP stream URL")
    parser.add_argument("--camera-id", default="CAM_01")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main():
    args = parse_args()
    source = int(args.source) if args.source.isdigit() else args.source

    _start_capture_thread(source, args.camera_id)

    print(f"Dashboard running at http://127.0.0.1:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
