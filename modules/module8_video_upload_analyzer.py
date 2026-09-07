"""
MODULE 8 — VIDEO UPLOAD ANALYZER
====================================
Runs the exact same two-level pipeline (Module 1-7) over an already-recorded
video file instead of a live camera feed, and produces a structured report:

    Upload -> Pipeline -> Timeline -> Alerts

Used by dashboard_app.py's `/api/upload` + `/api/upload/<job_id>` +
`/api/uploads` endpoints to power the "Upload Video" dashboard page. Runs in
a background thread so the Flask server stays responsive; progress/results
are polled by job_id.

Jobs are kept in memory (fast to update every frame) AND persisted to a JSON
file on disk, so:
  - refreshing the "Upload Video" page still shows every past upload and its
    result, instead of the list going blank (the previous version only ever
    rendered job cards client-side in the DOM, with nothing server-side to
    repopulate them from on page load);
  - restarting the dashboard process doesn't lose upload history either.
"""
import json
import time
import uuid
import threading
from pathlib import Path

import cv2

from modules.pipeline import SurveillancePipeline
from modules.utils import setup_logger

logger = setup_logger(__name__)

_JOBS = {}          # job_id -> job dict (in-memory, kept in sync with disk)
_JOBS_LOCK = threading.Lock()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STORE_PATH = _PROJECT_ROOT / "data" / "upload_jobs.json"
_LAST_PERSIST_AT = {"t": 0.0}
_PERSIST_MIN_INTERVAL = 1.0   # seconds — throttle disk writes during per-frame progress updates


def _load_jobs_from_disk():
    if not _STORE_PATH.exists():
        return
    try:
        with open(_STORE_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        logger.warning(f"Could not read {_STORE_PATH}; starting with an empty upload history.")
        return

    with _JOBS_LOCK:
        for job_id, job in data.items():
            # Any job that was still "queued"/"processing" when the process
            # last stopped had its background thread die with it — there is
            # nothing left running to finish it, so surface that honestly
            # instead of leaving a spinner that will never complete.
            if job.get("status") in ("queued", "processing"):
                job["status"] = "error"
                job["error"] = "Interrupted (server restarted while this job was running). Please re-upload."
                job["progress"] = job.get("progress") or 0.0
            _JOBS[job_id] = job


def _persist_jobs(force: bool = False):
    now = time.time()
    if not force and (now - _LAST_PERSIST_AT["t"]) < _PERSIST_MIN_INTERVAL:
        return
    _LAST_PERSIST_AT["t"] = now
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _JOBS_LOCK:
            snapshot = json.dumps(_JOBS)
        tmp_path = _STORE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            f.write(snapshot)
        tmp_path.replace(_STORE_PATH)   # atomic on POSIX — avoids a half-written file on crash
    except Exception:
        logger.exception("Failed to persist upload job state to disk")


_load_jobs_from_disk()


def _new_job(video_path: str, camera_id: str, original_filename: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "video_path": video_path,
            "filename": original_filename,
            "camera_id": camera_id,
            "status": "queued",       # queued -> processing -> done -> error
            "progress": 0.0,
            "total_frames": 0,
            "processed_frames": 0,
            "elapsed_seconds": 0.0,
            "eta_seconds": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "timeline": [],           # per-alert events with a video-relative timestamp
            "summary": {},
            "error": None,
        }
    _persist_jobs(force=True)
    return job_id


def get_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS[job_id]) if job_id in _JOBS else None


def list_jobs() -> list:
    """All known upload jobs (this session + persisted from disk), most recent first."""
    with _JOBS_LOCK:
        jobs = [dict(j) for j in _JOBS.values()]
    jobs.sort(key=lambda j: j.get("created_at") or j.get("started_at") or 0, reverse=True)
    return jobs


def _pick_frame_stride(duration_seconds: float) -> int:
    """
    Adaptive frame stride so that upload analysis time scales sub-linearly
    with video length instead of every video — a 20-second clip and a
    20-minute clip — using the same fixed stride (which made long videos
    painfully slow and made very short ones needlessly coarse-grained).
    Longer videos are sampled more sparsely; short ones stay fine-grained
    for better rule-timing accuracy (loitering, running, etc.).
    """
    if duration_seconds <= 0:
        return 6  # unknown duration (bad metadata) -> the previous fixed default
    if duration_seconds <= 30:
        return 3
    if duration_seconds <= 120:
        return 6
    if duration_seconds <= 300:
        return 10
    return 15


def start_analysis(video_path: str, camera_id: str = "UPLOAD", original_filename: str = None) -> str:
    """Kicks off background analysis of an uploaded video file. Returns a job_id."""
    job_id = _new_job(video_path, camera_id, original_filename or Path(video_path).name)
    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def _run_job(job_id: str):
    job = _JOBS[job_id]
    job["status"] = "processing"
    job["started_at"] = time.time()
    _persist_jobs(force=True)

    try:
        cap = cv2.VideoCapture(job["video_path"])
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.release()
        job["total_frames"] = total
        duration_estimate = (total / native_fps) if (total and native_fps) else 0.0
        frame_stride = _pick_frame_stride(duration_estimate)
        logger.info(f"Upload job {job_id}: ~{duration_estimate:.1f}s video, "
                    f"using adaptive frame_stride={frame_stride}")

        pipeline = SurveillancePipeline(source=job["video_path"], camera_id=job["camera_id"],
                                         save_evidence=True, realtime=False,
                                         frame_stride=frame_stride)

        video_start_wallclock = time.time()
        seen_alert_keys = set()
        seen_warning_keys = set()

        for _ in pipeline.frames():
            # pipeline.latest_stats["frame_index"] tracks the actual position in the
            # source video (it accounts for the frame stride, i.e. it can jump by
            # more than 1 per yielded frame), so progress/timeline timestamps stay
            # accurate even though not every frame is individually processed.
            frame_index = pipeline.latest_stats.get("frame_index") or job["processed_frames"] + 1
            job["processed_frames"] = frame_index
            if total > 0:
                # progress is a 0..1 fraction — the frontend multiplies by 100 for display
                job["progress"] = round(min(1.0, job["processed_frames"] / total), 3)
            else:
                # Some containers (webm, variable-framerate files, etc.) don't report a
                # reliable CAP_PROP_FRAME_COUNT, so we genuinely don't know the total.
                # Leave progress as None -> frontend shows an indeterminate spinner
                # instead of freezing on "0%" for the whole job.
                job["progress"] = None

            elapsed = time.time() - job["started_at"]
            job["elapsed_seconds"] = round(elapsed, 1)
            if job["progress"]:
                remaining = elapsed * (1 - job["progress"]) / job["progress"]
                job["eta_seconds"] = round(remaining, 1)

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

            _persist_jobs()   # throttled — at most once per _PERSIST_MIN_INTERVAL

        job["timeline"].sort(key=lambda e: e["video_time_s"])

        n_critical = sum(1 for e in job["timeline"] if e["type"] == "critical")
        n_warning = sum(1 for e in job["timeline"] if e["type"] == "warning")
        job["summary"] = {
            "duration_seconds": round(job["processed_frames"] / max(native_fps, 1e-6), 1),
            "frames_analyzed": job["processed_frames"],
            "critical_alerts": n_critical,
            "level1_warnings": n_warning,
            "processing_time_seconds": round(time.time() - video_start_wallclock, 1),
            "frame_stride_used": frame_stride,
        }
        # Also expose the fields flat on the job dict (in the names the dashboard
        # upload.js reads) so the finished-card summary line actually populates.
        job["total_alerts"] = n_critical + n_warning
        job["ai_verified"] = n_critical
        job["duration"] = job["summary"]["duration_seconds"]
        job["status"] = "done"
        job["progress"] = 1.0

    except Exception as e:
        logger.exception(f"Upload analysis job {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job["finished_at"] = time.time()
        _persist_jobs(force=True)
