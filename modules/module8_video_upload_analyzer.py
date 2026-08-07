"""
MODULE 8 — VIDEO UPLOAD ANALYZER
====================================
Runs the exact same two-level pipeline (Module 1-7) over an already-recorded
video file instead of a live camera feed, and produces a structured report:

    Upload -> Pipeline -> Timeline -> Alerts

Used by dashboard_app.py's `/api/upload` + `/api/upload/<job_id>` endpoints
to power the "Upload Video" dashboard page. Runs in a background thread so
the Flask server stays responsive; progress/results are polled by job_id.
"""
import time
import uuid
import threading
from pathlib import Path

import cv2

from modules.pipeline import SurveillancePipeline
from modules.utils import setup_logger

logger = setup_logger(__name__)

_JOBS = {}          # job_id -> job dict (in-memory; fine for a single-process demo dashboard)
_JOBS_LOCK = threading.Lock()


def _new_job(video_path: str, camera_id: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "video_path": video_path,
            "camera_id": camera_id,
            "status": "queued",       # queued -> processing -> done -> error
            "progress": 0.0,
            "total_frames": 0,
            "processed_frames": 0,
            "started_at": None,
            "finished_at": None,
            "timeline": [],           # per-alert events with a video-relative timestamp
            "summary": {},
            "error": None,
        }
    return job_id


def get_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS[job_id]) if job_id in _JOBS else None


def start_analysis(video_path: str, camera_id: str = "UPLOAD") -> str:
    """Kicks off background analysis of an uploaded video file. Returns a job_id."""
    job_id = _new_job(video_path, camera_id)
    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def _run_job(job_id: str):
    job = _JOBS[job_id]
    job["status"] = "processing"
    job["started_at"] = time.time()

    try:
        cap = cv2.VideoCapture(job["video_path"])
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.release()
        job["total_frames"] = total

        pipeline = SurveillancePipeline(source=job["video_path"], camera_id=job["camera_id"],
                                         save_evidence=True, realtime=False)

        video_start_wallclock = time.time()
        seen_alert_keys = set()
        seen_warning_keys = set()

        for _ in pipeline.frames():
            job["processed_frames"] += 1
            if total > 0:
                job["progress"] = round(min(1.0, job["processed_frames"] / total), 3)

            video_time_s = job["processed_frames"] / max(native_fps, 1e-6)

            for alert in pipeline.latest_stats["recent_alerts"]:
                key = (alert["track_id"], alert["time"], alert["risk_score"])
                if key in seen_alert_keys:
                    continue
                seen_alert_keys.add(key)
                job["timeline"].append({
                    "type": "critical",
                    "video_time_s": round(video_time_s, 2),
                    **alert,
                })

            for warn in pipeline.latest_stats["recent_warnings"]:
                key = (warn["track_id"], warn["time"], warn["risk_score"])
                if key in seen_warning_keys:
                    continue
                seen_warning_keys.add(key)
                job["timeline"].append({
                    "type": "warning",
                    "video_time_s": round(video_time_s, 2),
                    **warn,
                })

        job["timeline"].sort(key=lambda e: e["video_time_s"])

        n_critical = sum(1 for e in job["timeline"] if e["type"] == "critical")
        n_warning = sum(1 for e in job["timeline"] if e["type"] == "warning")
        job["summary"] = {
            "duration_seconds": round(job["processed_frames"] / max(native_fps, 1e-6), 1),
            "frames_analyzed": job["processed_frames"],
            "critical_alerts": n_critical,
            "level1_warnings": n_warning,
            "processing_time_seconds": round(time.time() - video_start_wallclock, 1),
        }
        job["status"] = "done"
        job["progress"] = 1.0

    except Exception as e:
        logger.exception(f"Upload analysis job {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job["finished_at"] = time.time()
