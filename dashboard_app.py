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
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from modules.pipeline import SurveillancePipeline
from modules.module7_evidence_manager import EvidenceManager
from modules.utils import rule_label
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

# Path to the "all fired alerts" log written by AlertOutputModule (Module 6)
# — Level-1 warnings (once per cooldown) + Level-2 confirmed criticals. Read
# fresh on every /api/analytics call, same pattern as EvidenceManager.read_all().
from modules.config import CFG as _CFG
_ALERTS_LOG_PATH = PROJECT_ROOT / getattr(_CFG.alerts, "alerts_log_path", "logs/alerts_log.csv")


def _read_alerts_log() -> list:
    """Every *fired* Level-1/Level-2 alert (not every-frame events, and not
    only confirmed criticals) — see modules/module6_alert_output.py."""
    if not _ALERTS_LOG_PATH.exists():
        return []
    import csv
    with open(_ALERTS_LOG_PATH, "r", newline="") as f:
        return list(csv.DictReader(f))


# Human-readable labels for each rule engine trigger — used by the History
# page for Level-1 rows (which have no AI-classified behaviour_class of
# their own, only the raw triggered_rules string from alerts_log.csv).
# Shared with modules/pipeline.py (used for immediate-critical bypass rows)
# via modules.utils.rule_label so the two never drift apart.
_label_from_rules = rule_label


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

    unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
    save_path = UPLOADS_DIR / unique_name
    file.save(str(save_path))

    # Each upload gets its own camera_id by default (UPLOAD_<random token>)
    # instead of every video sharing the literal same "UPLOAD" camera_id —
    # otherwise Analytics could never distinguish one uploaded video's
    # alerts from another's, no matter what filtering the frontend offered.
    # A plain unix-timestamp-based id isn't good enough here since two
    # uploads submitted within the same second would collide; the random
    # hex token guarantees each upload is distinguishable.
    # The person can still type a specific Camera ID to group several
    # uploads together (e.g. re-analyzing the same physical camera's clips).
    camera_id = request.form.get("camera_id", "").strip() or f"UPLOAD_{uuid.uuid4().hex[:8]}"
    job_id = upload_analyzer.start_analysis(str(save_path), camera_id=camera_id, original_filename=filename)
    return jsonify({"ok": True, "job_id": job_id, "camera_id": camera_id})


@app.route("/api/uploads")
def api_list_uploads():
    """All upload jobs (past + in-progress), most recent first — used to
    repopulate the Upload Video page's job list on page load/refresh."""
    return jsonify({"ok": True, "jobs": upload_analyzer.list_jobs()})


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
    """Alert History feed. Combines two sources so the page shows *every*
    fired alert, not only the rare Level-2/AI-confirmed ones that get a
    snapshot+clip saved:
      - evidence_log.csv  -> Level-2, AI-verified "critical" incidents,
                              with saved snapshot/video evidence.
      - alerts_log.csv     -> Level-1 rule-based "warning" incidents (fight,
                              restricted zone, abandoned object, etc.) that
                              fired a beep/dashboard alert but never escalated
                              (or hasn't yet) to a confirmed Level-2 critical.
    Level-2 rows already exist in alerts_log too (module6 logs both), so only
    that file's level=="1" rows are merged in here to avoid duplicates.
    """
    evidence_rows = _evidence.read_all()
    # Evidence-only index-from-end, computed BEFORE merging, so the existing
    # /api/evidence/<n>/false_positive endpoint (which only makes sense for
    # AI-confirmed incidents with saved evidence) keeps working unchanged.
    evidence_rows = list(reversed(evidence_rows))
    for i, r in enumerate(evidence_rows):
        r["type"] = "critical"
        r["evidence_index"] = i

    alert_rows = [r for r in _read_alerts_log() if str(r.get("level")) == "1"]
    alert_rows = list(reversed(alert_rows))
    for r in alert_rows:
        r["type"] = "warning"
        r["behaviour_class"] = _label_from_rules(r.get("triggered_rules"))
        r["image_path"] = ""
        r["video_path"] = ""
        r["false_positive"] = ""
        r["evidence_index"] = None

    rows = evidence_rows + alert_rows
    rows.sort(key=lambda r: float(r.get("timestamp") or 0), reverse=True)

    date_filter = request.args.get("date")
    camera_filter = request.args.get("camera_id")
    if date_filter:
        rows = [r for r in rows if r.get("date") == date_filter]
    if camera_filter:
        rows = [r for r in rows if r.get("camera_id") == camera_filter]

    for r in rows:
        r["image_url"] = f"/evidence/{r['image_path']}" if r.get("image_path") else None
        r["video_url"] = f"/evidence/{r['video_path']}" if r.get("video_path") else None

    n_critical = sum(1 for r in rows if r["type"] == "critical")
    return jsonify({
        "ok": True,
        "count": len(rows),
        "critical_count": n_critical,
        "warning_count": len(rows) - n_critical,
        "incidents": rows,
    })


@app.route("/api/evidence/<int:evidence_index>/false_positive", methods=["POST"])
def api_mark_false_positive(evidence_index):
    """evidence_index is the "evidence_index" field returned per-row by
    /api/evidence (only present on type=="critical" rows) — NOT a position
    in the merged history list, which also contains Level-1 warning rows
    that have no evidence_log.csv entry to flag."""
    ok = _evidence.mark_false_positive(evidence_index)
    return jsonify({"ok": ok})


@app.route("/evidence/<path:filename>")
def serve_evidence(filename):
    """Serves saved incident snapshots/clips from <project_root>/Evidence/..."""
    return send_from_directory(str(PROJECT_ROOT), filename)


# ======================================================================
# Analytics
# ======================================================================
@app.route("/api/cameras")
def api_cameras():
    """Distinct camera_id values seen across alerts + evidence + upload jobs
    — powers the Analytics page's per-video/per-camera filter dropdown."""
    ids = set()
    for r in _read_alerts_log():
        if r.get("camera_id"):
            ids.add(r["camera_id"])
    for r in _evidence.read_all():
        if r.get("camera_id"):
            ids.add(r["camera_id"])
    for job in upload_analyzer.list_jobs():
        if job.get("camera_id"):
            ids.add(job["camera_id"])
    return jsonify({"ok": True, "camera_ids": sorted(ids)})


@app.route("/api/analytics")
def api_analytics():
    camera_filter = request.args.get("camera_id", "").strip()

    evidence_rows = _evidence.read_all()          # Level-2, AI-verified & confirmed only
    alert_rows = _read_alerts_log()                # every fired Level-1 warning + Level-2 critical
    if camera_filter:
        evidence_rows = [r for r in evidence_rows if r.get("camera_id") == camera_filter]
        alert_rows = [r for r in alert_rows if r.get("camera_id") == camera_filter]
    today = datetime.now().strftime("%Y-%m-%d")

    total_alerts = len(alert_rows)
    today_alerts = [r for r in alert_rows if r.get("date") == today]
    ai_verified = [r for r in evidence_rows if str(r.get("level")) == "2"]
    false_positives = [r for r in evidence_rows if r.get("false_positive") in ("1", 1, True, "true")]

    fp_rate = (len(false_positives) / len(evidence_rows)) if evidence_rows else 0.0
    avg_risk = (sum(float(r["risk_score"]) for r in alert_rows if r.get("risk_score")) / total_alerts) \
        if total_alerts else 0.0

    # hourly timeline (by hour-of-day, across all logged days) — built from
    # every fired alert (Level-1 + Level-2), so this is populated even when
    # no Level-2 "critical + AI-confirmed" incident has happened yet.
    hourly_counts = defaultdict(int)
    heatmap = defaultdict(lambda: defaultdict(int))  # camera_id -> hour -> count
    for r in alert_rows:
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
        "camera_id": camera_filter or None,
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
