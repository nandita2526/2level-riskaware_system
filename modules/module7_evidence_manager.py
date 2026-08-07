"""
MODULE 7 — EVIDENCE MANAGER
===============================
Saves suspicious-scene evidence to disk whenever a Level-2, AI-verified
critical alert fires:

    Evidence/
        Images/<YYYY-MM-DD>/<HH-MM-SS>.jpg
        Videos/<YYYY-MM-DD>/Incident_NN.mp4

Also appends a structured row (camera_id, risk score, AI confidence,
timestamp, behaviour class, and the saved file paths) to
logs/evidence_log.csv, which powers the dashboard's Alert History / Evidence
Viewer / Analytics pages.

Video clips are built from a short rolling pre-event frame buffer (so the
clip captures the seconds *before* the trigger, not just after) plus a few
post-event frames pushed in by the pipeline as they arrive.
"""
import csv
import time
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from modules.config import CFG
from modules.utils import setup_logger

logger = setup_logger(__name__)

CSV_FIELDS = ["timestamp", "date", "time", "camera_id", "track_id", "behaviour_class",
              "risk_score", "ai_confidence", "level", "triggered_rules",
              "image_path", "video_path", "false_positive"]


class EvidenceManager:
    def __init__(self, cfg=None, root_dir: str = None, log_path: str = None):
        cfg = (cfg or CFG).evidence
        self.cfg = cfg
        self.enabled_images = cfg.save_images
        self.enabled_videos = cfg.save_video_clips
        self.clip_fps = cfg.clip_fps
        self.pre_event_frames = int(cfg.clip_pre_event_seconds * cfg.clip_fps)
        self.post_event_frames = int(cfg.clip_post_event_seconds * cfg.clip_fps)

        project_root = Path(__file__).resolve().parent.parent
        self.root_dir = Path(root_dir or (project_root / cfg.root_dir))
        self.log_path = Path(log_path or (project_root / cfg.log_path))

        (self.root_dir / "Images").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "Videos").mkdir(parents=True, exist_ok=True)
        self._init_log()

        # rolling pre-event frame buffer, shared across tracks on this camera
        self._frame_buffer = deque(maxlen=max(1, self.pre_event_frames))
        self._incident_counters = {}   # date_str -> next incident number
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def push_frame(self, frame_bgr: np.ndarray):
        """Called once per pipeline frame so a pre-event buffer is always warm."""
        self._frame_buffer.append(frame_bgr.copy())

    # ------------------------------------------------------------------
    def save_incident(self, camera_id: str, track_id, behaviour_class: str,
                       risk_score: float, ai_confidence: float, level: int,
                       triggered_rules: list, snapshot_bgr: np.ndarray = None,
                       post_event_frames: list = None) -> dict:
        """
        Saves an image snapshot + (optionally) a short video clip for a
        confirmed incident, and appends a row to evidence_log.csv.
        Returns a dict describing what was written (paths are relative to the
        project root so they can be turned into dashboard URLs directly).
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")

        image_rel, video_rel = "", ""

        if self.enabled_images and snapshot_bgr is not None and snapshot_bgr.size > 0:
            image_rel = self._save_image(snapshot_bgr, date_str, time_str)

        if self.enabled_videos:
            video_rel = self._save_clip(date_str, post_event_frames or [])

        row = {
            "timestamp": time.time(),
            "date": date_str,
            "time": now.strftime("%H:%M:%S"),
            "camera_id": camera_id,
            "track_id": track_id,
            "behaviour_class": behaviour_class,
            "risk_score": round(float(risk_score), 4),
            "ai_confidence": round(float(ai_confidence), 4),
            "level": level,
            "triggered_rules": "|".join(triggered_rules or []),
            "image_path": image_rel,
            "video_path": video_rel,
            "false_positive": "",
        }
        self._append_log(row)
        return row

    # ------------------------------------------------------------------
    def _save_image(self, snapshot_bgr: np.ndarray, date_str: str, time_str: str) -> str:
        day_dir = self.root_dir / "Images" / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{time_str}.jpg"
        path = day_dir / filename
        # avoid overwriting if two incidents land in the same second
        n = 1
        while path.exists():
            filename = f"{time_str}_{n}.jpg"
            path = day_dir / filename
            n += 1
        cv2.imwrite(str(path), snapshot_bgr)
        return str(path.relative_to(self.root_dir.parent))

    def _save_clip(self, date_str: str, post_event_frames: list) -> str:
        frames = list(self._frame_buffer) + list(post_event_frames)
        if not frames:
            return ""

        day_dir = self.root_dir / "Videos" / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            n = self._incident_counters.get(date_str, self._count_existing_clips(day_dir) + 1)
            filename = f"Incident_{n:02d}.mp4"
            self._incident_counters[date_str] = n + 1

        path = day_dir / filename
        h, w = frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, self.clip_fps, (w, h))
        try:
            for f in frames:
                if f.shape[:2] != (h, w):
                    f = cv2.resize(f, (w, h))
                writer.write(f)
        finally:
            writer.release()

        return str(path.relative_to(self.root_dir.parent))

    @staticmethod
    def _count_existing_clips(day_dir: Path) -> int:
        return len(list(day_dir.glob("Incident_*.mp4")))

    # ------------------------------------------------------------------
    def _init_log(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            with open(self.log_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    def _append_log(self, row: dict):
        with open(self.log_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)

    # ------------------------------------------------------------------
    def read_all(self) -> list:
        """Returns every logged incident as a list of dicts (most recent last)."""
        if not self.log_path.exists():
            return []
        with open(self.log_path, "r", newline="") as f:
            return list(csv.DictReader(f))

    def mark_false_positive(self, row_index_from_end: int) -> bool:
        """Flags the Nth-most-recent incident (0 = latest) as a false positive."""
        rows = self.read_all()
        if not rows or row_index_from_end >= len(rows):
            return False
        target = len(rows) - 1 - row_index_from_end
        rows[target]["false_positive"] = "1"
        with open(self.log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return True
