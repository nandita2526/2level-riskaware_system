"""
PIPELINE ORCHESTRATOR (TWO-LEVEL ARCHITECTURE)
==================================================
Wires the modules together end-to-end for every frame, implementing the
Level-1 (rule-based) -> Level-2 (AI-verification) escalation flow:

  Module 1 (Video Capture)       -> raw frame
  Module 2 (Preprocessing)        -> resized + normalised frame
  Module 3 (Detection & Tracking) -> tracked person + object bounding boxes
  Module 4A (Rule Engine)         -> Level-1 risk score (no AI)
        risk < threshold  -> Module 6 Level-1 alert (dashboard + beep), STOP
        risk >= threshold -> escalate ↓
  Module 4B (CNN -> LSTM)         -> AI verification -> P_threat
  Module 5 (Risk Assessment)      -> combined final score, tier, 3-window confirm
  Module 6 (Alert & Output)       -> siren + push notification + CSV log + overlay
  Module 7 (Evidence Manager)     -> saves image/video evidence for confirmed critical alerts

Used identically by main.py (OpenCV window), dashboard_app.py (live web
dashboard), and modules/module8_video_upload_analyzer.py (offline upload
analysis) — all just iterate frames from this class and read back
`pipeline.latest_stats`.
"""
import time
from collections import defaultdict

import cv2

from modules.config import CFG
from modules.module1_video_capture import VideoCaptureModule
from modules.module2_preprocessing import PreprocessingModule
from modules.module3_detection_tracking import DetectionTrackingModule
from modules.module4a_rule_engine import RuleEngineModule
from modules.module4_behaviour_classification import BehaviourClassificationModule
from modules.module5_risk_assessment import RiskAssessmentModule
from modules.module6_alert_output import AlertOutputModule
from modules.module7_evidence_manager import EvidenceManager
from modules.utils import FrameSequenceBuffer, PersistenceTracker, compute_crowd_density, rule_label, setup_logger

logger = setup_logger(__name__)


class SurveillancePipeline:
    def __init__(self, source=0, camera_id: str = "CAM_01", save_evidence: bool = True,
                 realtime: bool = True, frame_stride: int = None):
        self.source = source
        self.camera_id = camera_id
        self.realtime = realtime
        # Optional override for the offline "Upload Video" frame stride
        # (see module8_video_upload_analyzer.py, which picks this adaptively
        # based on the uploaded video's own length so a 3-minute clip and a
        # 30-second clip both finish analysis in a similar, reasonable wall-
        # clock time). Falls back to config.yaml's video.upload_frame_stride
        # when not given.
        self.frame_stride_override = frame_stride

        # --- instantiate the modules ---
        self.m2_preprocess = PreprocessingModule()
        self.m3_detect_track = DetectionTrackingModule()
        self.m4a_rules = RuleEngineModule()
        self.m4_behaviour = BehaviourClassificationModule()
        self.m5_risk = RiskAssessmentModule()
        self.m7_evidence = EvidenceManager() if save_evidence else None
        # Voice alerts (pyttsx3) speak in real wall-clock time and make no
        # sense for an offline/background job (nobody is there to hear them)
        # — they also block the processing loop for the duration of each
        # spoken message, which noticeably slows down "Upload Video" analysis
        # if a TTS engine happens to be installed. Disable audio whenever the
        # pipeline isn't driving a live feed.
        self.m6_alert_output = AlertOutputModule(evidence_manager=self.m7_evidence, realtime=realtime)

        self.seq_len = CFG.video.sequence_length
        self.frame_buffer = FrameSequenceBuffer(seq_len=self.seq_len,
                                                 feature_dim=CFG.feature_extractor.feature_dim)
        self.persistence_tracker = PersistenceTracker(window_seconds=CFG.persistence_window_seconds)

        # live stats consumed by the dashboard (updated every frame)
        self.latest_stats = {
            "frame_count": 0,
            "frame_index": 0,      # position within the source video (accounts for frame_stride)
            "fps": 0.0,
            "active_tracks": {},          # track_id -> {class, risk, tier, level, ...}
            "tier_counts": defaultdict(int),
            "recent_alerts": [],          # last N Level-2, AI-verified critical alerts
            "recent_warnings": [],        # last N Level-1 rule-based warnings
            "started_at": time.time(),
        }
        self._fps_smoother = []
        self._scene_event_counter = 0   # gives each fired scene_hazard event a unique synthetic track_id

        if not self.m4_behaviour.loaded_pretrained:
            logger.warning("No trained LSTM checkpoint found at "
                            f"'{CFG.lstm.checkpoint_path}'. AI-verification predictions will be "
                            "random until you run training/train_lstm.py. The pipeline still runs end-to-end.")

    def frames(self):
        """Generator: Module 1 (capture) -> per-frame processing -> yields the annotated display frame."""
        # In realtime mode (live camera/RTSP), throttle to the configured target FPS
        # and process every frame. In offline mode (uploaded video analysis), read
        # as fast as the decoder allows AND only run the (expensive) detection /
        # classification stages on every Nth frame, controlled by
        # video.upload_frame_stride, so large uploads finish in a reasonable time.
        target_fps = CFG.video.target_fps if self.realtime else 10_000
        if self.realtime:
            frame_stride = 1
        elif self.frame_stride_override is not None:
            frame_stride = max(1, int(self.frame_stride_override))
        else:
            frame_stride = max(1, int(getattr(CFG.video, "upload_frame_stride", 1)))
        m1_capture = VideoCaptureModule(source=self.source, target_fps=target_fps, frame_stride=frame_stride)
        logger.info(f"Pipeline started on source={self.source} "
                    f"(realtime={self.realtime}, frame_stride={frame_stride}).")
        try:
            for raw in m1_capture:
                t0 = time.time()
                display_frame = self._process_frame(raw)
                self.latest_stats["frame_index"] = raw.get("frame_index", self.latest_stats["frame_count"] + 1)
                self._update_fps(time.time() - t0)
                yield display_frame
        finally:
            m1_capture.release()
            logger.info("Pipeline stopped.")

    def _update_fps(self, frame_time: float):
        self._fps_smoother.append(frame_time)
        if len(self._fps_smoother) > 20:
            self._fps_smoother.pop(0)
        avg_time = sum(self._fps_smoother) / len(self._fps_smoother)
        self.latest_stats["fps"] = round(1.0 / avg_time, 1) if avg_time > 0 else 0.0
        self.latest_stats["frame_count"] += 1

    def _process_frame(self, raw: dict):
        # Module 2 — Preprocessing
        pre = self.m2_preprocess.process(raw["raw_frame"])
        display_frame = pre["display_frame"]

        if self.m7_evidence is not None:
            self.m7_evidence.push_frame(display_frame)

        # ---------------- Scene-level hazard check (fire / explosion / blast) ----------------
        # Runs unconditionally, BEFORE the "no detections -> return early" branches below,
        # because a blast/fire has no person or watched-object for YOLO to detect at all —
        # every other rule in this file only ever evaluates for detected persons, so without
        # this a video like an exploding car in an empty lot would always show 0 alerts.
        self._check_scene_hazard(raw, display_frame)

        # Module 3 — Detection & Tracking (persons + watched objects e.g. bags)
        detections = self.m3_detect_track.detect_and_track(display_frame)

        self.latest_stats["active_tracks"] = {}
        self.latest_stats["tier_counts"] = defaultdict(int)

        if not detections:
            return display_frame

        persons = self.m3_detect_track.persons(detections)
        objects = self.m3_detect_track.objects(detections)
        h, w = display_frame.shape[:2]

        # ---------------- LEVEL 1 — Rule Engine (no AI) ----------------
        # Use the video-relative clock (not wall-clock) so time-based rules
        # (loitering, running, fall, abandoned-object) measure actual video
        # duration correctly whether this is a live feed (video_time tracks
        # wall-clock 1:1) or an offline upload analysis (which runs faster
        # or slower than real-time — see module1_video_capture.py).
        video_time = raw["video_time"]
        rule_results = self.m4a_rules.evaluate(persons, objects, (h, w), timestamp=video_time)

        if not persons:
            return display_frame

        crops = [self.m3_detect_track.crop(display_frame, d.bbox) for d in persons]
        c_density = compute_crowd_density(len(persons), float(h * w))

        for det, crop in zip(persons, crops):
            rule_result = rule_results.get(det.track_id)
            if rule_result is None:
                continue

            level1 = self.m5_risk.evaluate_level1(rule_result)

            # Fire the Level-1 dashboard alert + beep for any triggered rule
            # ("warning" tier AND "high" tier — see module6_alert_output.py's
            # trigger_level1() bugfix note for why "high" must be included
            # here too), and log every track regardless of tier. The method
            # itself is cooldown-gated (level1_cooldown_seconds), so its
            # return value tells us exactly when a *new* alert actually fired
            # — use that (instead of re-checking the tier here) so the
            # dashboard's recent_warnings / Analytics timeline aren't flooded
            # with a duplicate entry on every single processed frame while a
            # track continues triggering the same rule.
            warning_fired = self.m6_alert_output.trigger_level1(det.track_id, level1, camera_id=self.camera_id)
            if warning_fired:
                self.latest_stats["recent_warnings"].insert(0, {
                    "track_id": det.track_id,
                    "risk_score": round(level1.score, 3),
                    "camera_id": self.camera_id,
                    "triggered_rules": level1.triggered_rules,
                    "time": time.strftime("%H:%M:%S"),
                })
                self.latest_stats["recent_warnings"] = self.latest_stats["recent_warnings"][:20]

            final_risk = level1
            predicted_class = "Normal"

            if self.m5_risk.is_critical_rule_bypass(level1.score):
                # ---------------- IMMEDIATE CRITICAL (severe rule bypass) ----------------
                # Fight / fall / restricted-zone / abandoned-object hits are already
                # near-certain threats — don't wait ~seq_len processed frames for the
                # CNN/LSTM AI-verification window to fill (the track may not even last
                # that long) before sounding the siren. See module5_risk_assessment.py.
                final_risk = self.m5_risk.evaluate_immediate_critical(level1)
                predicted_class = rule_label(level1.triggered_rules)

                snapshot = self.m3_detect_track.crop(display_frame, det.bbox)
                self.m6_alert_output.trigger_level2(
                    det.track_id, final_risk, camera_id=self.camera_id,
                    behaviour_class=predicted_class, snapshot_bgr=snapshot,
                )
                self.latest_stats["recent_alerts"].insert(0, {
                    "track_id": det.track_id,
                    "risk_score": round(final_risk.score, 3),
                    "camera_id": self.camera_id,
                    "behaviour_class": predicted_class,
                    "triggered_rules": final_risk.triggered_rules,
                    "time": time.strftime("%H:%M:%S"),
                })
                self.latest_stats["recent_alerts"] = self.latest_stats["recent_alerts"][:20]

            else:
                # ---------------- LEVEL 2 — AI Verification (CNN -> LSTM) ----------------
                # Runs for EVERY tracked person, not only ones that already tripped a
                # Level-1 rule. Level-1 only covers gross motion (loitering, running,
                # crowds, fights, falls, restricted-zone entry, abandoned objects) —
                # subtle behaviours like a quick "Suspicious_Theft_Gesture" at a
                # counter/shelf never move fast or far enough to trip any of those
                # rules, so gating the CNN/LSTM behind level1.score >= threshold meant
                # the AI verifier (which IS trained to recognise that exact class) was
                # simply never invoked for that footage and it fell through to
                # "Normal" by default. Level-1's rule score still feeds evaluate_level2's
                # weighted final_score and is still what the operator sees as
                # `triggered_rules`; it just no longer gatekeeps whether AI runs at all.
                feat = self.m4_behaviour.extract_features([crop])[0]
                self.frame_buffer.push(det.track_id, feat)

                if not self.frame_buffer.is_ready(det.track_id):
                    self.m6_alert_output.draw_overlay(
                        display_frame, det.track_id, det.bbox, tier="high",
                        label=f"ID{det.track_id} Verifying... (buffering AI window)")
                    self._record_track_stats(det.track_id, "Buffering", level1, c_density)
                    continue

                sequence = self.frame_buffer.get_sequence(det.track_id)
                predicted_class, probs, p_threat = self.m4_behaviour.classify_sequence(sequence)

                is_suspicious = predicted_class != "Normal"
                self.persistence_tracker.update(det.track_id, is_suspicious, timestamp=video_time)

                final_risk = self.m5_risk.evaluate_level2(det.track_id, level1, p_threat)

                snapshot = self.m3_detect_track.crop(display_frame, det.bbox)
                self.m6_alert_output.trigger_level2(
                    det.track_id, final_risk, camera_id=self.camera_id,
                    behaviour_class=predicted_class, snapshot_bgr=snapshot,
                )

                if final_risk.tier == "critical" and final_risk.confirmed:
                    self.latest_stats["recent_alerts"].insert(0, {
                        "track_id": det.track_id,
                        "risk_score": round(final_risk.score, 3),
                        "camera_id": self.camera_id,
                        "behaviour_class": predicted_class,
                        "triggered_rules": final_risk.triggered_rules,
                        "time": time.strftime("%H:%M:%S"),
                    })
                    self.latest_stats["recent_alerts"] = self.latest_stats["recent_alerts"][:20]

            label = (f"ID{det.track_id} {predicted_class} R={final_risk.score:.2f} "
                     f"[{final_risk.tier}{'*AI' if final_risk.ai_verified else ''}]")
            self.m6_alert_output.draw_overlay(display_frame, det.track_id, det.bbox, final_risk.tier, label)
            self._record_track_stats(det.track_id, predicted_class, final_risk, c_density)

        return display_frame

    def _check_scene_hazard(self, raw: dict, display_frame):
        """Fire / explosion / blast — a whole-scene event, not a per-person
        one. Fires straight to a confirmed Level-2 "critical" alert (siren +
        evidence save + push notification + browser sound), the same as a
        fight/fall/restricted-zone rule bypass, since by the time this is
        visually confirmed it's already unambiguous and time-critical — see
        RuleEngineModule.evaluate_scene() for the detection logic itself.
        """
        video_time = raw.get("video_time", time.time())
        hazard = self.m4a_rules.evaluate_scene(display_frame, timestamp=video_time)
        if not hazard.is_triggered:
            return

        self._scene_event_counter += 1
        scene_id = f"SCENE_{self._scene_event_counter}"
        label = rule_label(hazard.triggered_rules)

        level1 = self.m5_risk.evaluate_level1(hazard)
        final_risk = level1
        if self.m5_risk.needs_ai_verification(level1.score) and \
                self.m5_risk.is_critical_rule_bypass(level1.score):
            final_risk = self.m5_risk.evaluate_immediate_critical(level1)

        warning_fired = self.m6_alert_output.trigger_level1(scene_id, level1, camera_id=self.camera_id)
        if warning_fired:
            self.latest_stats["recent_warnings"].insert(0, {
                "track_id": scene_id,
                "risk_score": round(level1.score, 3),
                "camera_id": self.camera_id,
                "triggered_rules": level1.triggered_rules,
                "time": time.strftime("%H:%M:%S"),
            })
            self.latest_stats["recent_warnings"] = self.latest_stats["recent_warnings"][:20]

        if final_risk.level == 2:
            self.m6_alert_output.trigger_level2(
                scene_id, final_risk, camera_id=self.camera_id,
                behaviour_class=label, snapshot_bgr=display_frame.copy(),
            )
            self.latest_stats["recent_alerts"].insert(0, {
                "track_id": scene_id,
                "risk_score": round(final_risk.score, 3),
                "camera_id": self.camera_id,
                "behaviour_class": label,
                "triggered_rules": final_risk.triggered_rules,
                "time": time.strftime("%H:%M:%S"),
            })
            self.latest_stats["recent_alerts"] = self.latest_stats["recent_alerts"][:20]

        cv2.putText(display_frame, f"HAZARD: {label}", (16, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.rectangle(display_frame, (4, 4),
                       (display_frame.shape[1] - 4, display_frame.shape[0] - 4), (0, 0, 255), 4)

    def _record_track_stats(self, track_id, predicted_class: str, risk_result, c_density: float):
        self.latest_stats["active_tracks"][track_id] = {
            "class": predicted_class,
            "risk_score": round(risk_result.score, 3),
            "tier": risk_result.tier,
            "level": risk_result.level,
            "ai_verified": risk_result.ai_verified,
            "p_threat": round(risk_result.p_threat, 3),
            "c_density": round(c_density, 3),
            "triggered_rules": risk_result.triggered_rules,
        }
        self.latest_stats["tier_counts"][risk_result.tier] += 1
