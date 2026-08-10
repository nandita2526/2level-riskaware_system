"""
MODULE 6 — ALERT & OUTPUT MODULE
===================================
- Level-1 (rule-based, "warning" tier): short soft beep + yellow dashboard
  alert. No AI, no evidence saved — just logged and shown so the operator
  stays aware while Module 4B verifies.
- Level-2 (AI-verified, "critical" tier, `confirmed=True`): full siren, red
  dashboard alert, evidence snapshot + video clip saved via Module 7, and a
  push notification (JSON webhook) to registered security personnel.
- Always writes a timestamped event-log row to storage (CSV), for every tier.
- Renders annotated bounding boxes and risk labels as an overlay on the live
  display / dashboard stream.
"""

import base64
import csv
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

from modules.config import CFG
from modules.utils import setup_logger

logger = setup_logger(__name__)

try:
    import pyttsx3
    _AUDIO_AVAILABLE = True
except (ImportError, OSError):
    _AUDIO_AVAILABLE = False


TIER_COLORS = {   # BGR colors for on-screen overlay
    "normal":   (0, 200, 0),
    "elevated": (0, 200, 255),
    "warning":  (0, 200, 255),
    "high":     (0, 128, 255),
    "critical": (0, 0, 255),
}


class AlertOutputModule:
    def __init__(self, cfg=None, log_path: str = None, evidence_manager=None):
        cfg = cfg or CFG
        self.audio_enabled = cfg.alerts.audio_enabled and _AUDIO_AVAILABLE

        # Voice messages spoken via pyttsx3 for each tier. Falls back to
        # sensible defaults if not present in config.
        self.level1_message = getattr(cfg.alerts, "level1_voice_message",
                                       "Warning. Suspicious activity detected.")
        self.level2_message = getattr(cfg.alerts, "level2_voice_message",
                                       "Critical alert. Confirmed suspicious activity. "
                                       "Security personnel, please respond immediately.")
        self.tts_rate = getattr(cfg.alerts, "tts_rate", 175)          # words per minute
        self.tts_volume = getattr(cfg.alerts, "tts_volume", 1.0)      # 0.0 - 1.0

        self.webhook_url = cfg.alerts.webhook_url
        self.webhook_timeout = cfg.alerts.webhook_timeout_seconds
        self.cooldown_seconds = cfg.alerts.cooldown_seconds
        self.level1_cooldown_seconds = cfg.alerts.level1_cooldown_seconds

        self._last_critical_time = {}   # track_id -> last time a Level-2 voice alert fired
        self._last_warning_time = {}    # track_id -> last time a Level-1 voice alert fired

        self._tts_engine = None
        if self.audio_enabled:
            try:
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty("rate", self.tts_rate)
                self._tts_engine.setProperty("volume", self.tts_volume)
            except Exception as e:
                logger.error(f"Failed to initialize pyttsx3 engine: {e}")
                self.audio_enabled = False

        if cfg.alerts.audio_enabled and not _AUDIO_AVAILABLE:
            logger.warning("pyttsx3 not available on this system — "
                            "voice alerts disabled, notifications/logging still work.")

        self.evidence_manager = evidence_manager  # Module 7 (optional; injected by pipeline)

        self.log_path = Path(log_path or (Path(__file__).resolve().parent.parent / "logs" / "event_log.csv"))
        self._init_event_log()

    # ---------------- Audio (pyttsx3 voice alerts) ----------------
    def play_siren(self):
        """Level-2, AI-verified critical alert — spoken critical-alert message."""
        self._speak(self.level2_message)

    def play_beep(self):
        """Level-1, rule-based warning alert — spoken warning message."""
        self._speak(self.level1_message)

    def _speak(self, message: str):
        if not self.audio_enabled or self._tts_engine is None:
            return
        try:
            self._tts_engine.say(message)
            self._tts_engine.runAndWait()
        except Exception as e:
            logger.error(f"Failed to play voice alert: {e}")

    # ---------------- Push notification ----------------
    def send_push_notification(self, payload: dict):
        try:
            requests.post(self.webhook_url, json=payload, timeout=self.webhook_timeout)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Push notification failed (is notifications/mock_server.py running?): {e}")

    # ---------------- CSV event log ----------------
    def _init_event_log(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            with open(self.log_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "datetime", "camera_id", "track_id", "level", "tier",
                                  "risk_score", "p_threat", "triggered_rules"])

    def write_event_log(self, camera_id: str, track_id, risk_result):
        with open(self.log_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                time.time(),
                datetime.now().isoformat(timespec="seconds"),
                camera_id, track_id, risk_result.level, risk_result.tier,
                round(risk_result.score, 4), round(risk_result.p_threat, 4),
                "|".join(risk_result.triggered_rules),
            ])

    # ---------------- Overlay rendering ----------------
    @staticmethod
    def draw_overlay(frame: np.ndarray, track_id, bbox: tuple, tier: str, label: str):
        """Renders annotated bounding boxes and risk labels on the live display/dashboard stream."""
        x1, y1, x2, y2 = [int(v) for v in bbox]
        color = TIER_COLORS.get(tier, (255, 255, 255))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        return frame

    # ---------------- Orchestration ----------------
    def _in_cooldown(self, store: dict, track_id, cooldown: float) -> bool:
        last = store.get(track_id)
        return last is not None and (time.time() - last) < cooldown

    def trigger_level1(self, track_id, risk_result, camera_id: str = "CAM_01") -> bool:
        """
        Called for every tracked person, every frame. Always writes the event
        log; fires the Level-1 warning beep (once per cooldown window) only
        when the rule engine actually triggered ("warning" tier).
        """
        self.write_event_log(camera_id, track_id, risk_result)

        if risk_result.tier != "warning":
            return False
        if self._in_cooldown(self._last_warning_time, track_id, self.level1_cooldown_seconds):
            return False

        self._last_warning_time[track_id] = time.time()
        logger.info(f"LEVEL-1 WARNING — track_id={track_id} rule_score={risk_result.score:.3f} "
                    f"rules={risk_result.triggered_rules}")
        self.play_beep()
        return True

    def trigger_level2(self, track_id, risk_result, camera_id: str = "CAM_01",
                        behaviour_class: str = "Unknown", snapshot_bgr: np.ndarray = None) -> bool:
        """
        Called only for tracks that were escalated to AI verification. Fires
        the full siren + evidence save + push notification once the AI has
        `confirmed` a critical tier, respecting the standard cooldown.
        """
        self.write_event_log(camera_id, track_id, risk_result)

        if risk_result.tier != "critical" or not risk_result.confirmed:
            return False
        if self._in_cooldown(self._last_critical_time, track_id, self.cooldown_seconds):
            return False

        self._last_critical_time[track_id] = time.time()

        logger.warning(f"LEVEL-2 CRITICAL ALERT (AI-verified) — track_id={track_id} "
                        f"risk={risk_result.score:.3f} P_threat={risk_result.p_threat:.2f} "
                        f"rules={risk_result.triggered_rules}")

        self.play_siren()

        evidence_row = None
        if self.evidence_manager is not None:
            evidence_row = self.evidence_manager.save_incident(
                camera_id=camera_id, track_id=track_id, behaviour_class=behaviour_class,
                risk_score=risk_result.score, ai_confidence=risk_result.p_threat,
                level=2, triggered_rules=risk_result.triggered_rules,
                snapshot_bgr=snapshot_bgr,
            )

        payload = {
            "event": "critical_risk_alert",
            "track_id": track_id,
            "camera_id": camera_id,
            "behaviour_class": behaviour_class,
            "risk_score": round(risk_result.score, 4),
            "p_threat": round(risk_result.p_threat, 4),
            "triggered_rules": risk_result.triggered_rules,
            "timestamp": time.time(),
        }
        if snapshot_bgr is not None:
            payload["snapshot_base64"] = self._encode_snapshot(snapshot_bgr)
        if evidence_row:
            payload["evidence"] = {"image_path": evidence_row["image_path"],
                                    "video_path": evidence_row["video_path"]}

        self.send_push_notification(payload)
        return True

    @staticmethod
    def _encode_snapshot(frame_bgr: np.ndarray) -> str:
        ok, buffer = cv2.imencode(".jpg", frame_bgr)
        if not ok:
            return ""
        return base64.b64encode(buffer.tobytes()).decode("utf-8")