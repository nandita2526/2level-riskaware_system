"""
MODULE 4A — LEVEL-1 RULE ENGINE (NO AI)
==========================================
Pure, deterministic, spatial/temporal rule checks over YOLO+ByteTrack
detections. No neural network is involved here — this is the fast,
always-on "Level 1" stage of the two-level architecture:

    Camera -> YOLO Detection -> Rule Engine -> Risk Score
                                     |
                    Risk < threshold -> Dashboard alert (yellow) + beep
                    Risk >= threshold -> escalate to Module 4B (AI verification)

Rules implemented (each independently toggle-able in config.yaml):
    - Loitering              : a person stays within a small radius for > N seconds
    - Restricted Zone        : a person's centroid enters a configured polygon
    - Running                : a person's centroid speed exceeds a threshold
    - Crowd Density          : too many persons simultaneously in frame
    - Abandoned Object       : a bag/suitcase/backpack is stationary with no
                                person nearby for > N seconds
    - Fight Detection        : two tracks are very close AND moving rapidly
                                relative to each other
    - Fall Detection         : a person's bounding-box aspect ratio flips from
                                "tall" (standing) to "wide" (fallen) quickly

Level-1 Risk Score = MAX(triggered rule scores) — a single strong rule is
enough to flag a track as high-risk and request AI verification (Slide-style
"OR" escalation logic, matches the "Rule Score" box in the architecture diagram).
"""
import time
import math
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import List, Dict

from modules.config import CFG


@dataclass
class RuleResult:
    score: float                 # Level-1 (rule-based) risk score in [0, 1]
    triggered_rules: List[str]   # e.g. ["loitering", "restricted_zone"]

    @property
    def is_triggered(self) -> bool:
        return len(self.triggered_rules) > 0


@dataclass
class _TrackHistory:
    positions: deque = field(default_factory=lambda: deque(maxlen=150))  # (t, cx, cy)
    bbox_history: deque = field(default_factory=lambda: deque(maxlen=30))  # (t, w, h)
    first_seen: float = None
    anchor: tuple = None          # centroid the loitering timer is anchored to


def _centroid(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def _point_in_polygon(point, polygon):
    """Standard ray-casting point-in-polygon test. polygon: list of (x, y)."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class RuleEngineModule:
    """
    Module 4A — evaluates all Level-1 rules for every currently tracked
    person (and watched object) and returns a per-track_id RuleResult.
    Maintains its own lightweight per-track history (position/time), separate
    from the CNN/LSTM feature buffer used by Module 4B.
    """

    def __init__(self, cfg=None):
        cfg = (cfg or CFG).rule_engine
        self.cfg = cfg

        self._persons: Dict[int, _TrackHistory] = defaultdict(_TrackHistory)
        self._objects: Dict[int, _TrackHistory] = defaultdict(_TrackHistory)

        if cfg.restricted_zone.enabled:
            self._zone_polygon = list(cfg.restricted_zone.polygon)
        else:
            self._zone_polygon = None

    # ------------------------------------------------------------------
    def evaluate(self, persons: list, objects: list, frame_shape: tuple,
                 timestamp: float = None) -> Dict[int, RuleResult]:
        """
        persons / objects: lists of modules.module3_detection_tracking.Detection
        frame_shape: (h, w) of the frame the bboxes are expressed in.
        Returns: {track_id: RuleResult} for every currently-detected person.
        """
        timestamp = timestamp if timestamp is not None else time.time()
        h, w = frame_shape[:2]

        self._update_histories(persons, objects, timestamp)
        self._forget_stale(persons, objects)

        results: Dict[int, RuleResult] = {}
        crowd_triggered = (self.cfg.crowd_density.enabled and
                            len(persons) >= self.cfg.crowd_density.count_threshold)

        for det in persons:
            triggered = []
            scores = []

            if self.cfg.loitering.enabled:
                s = self._check_loitering(det, timestamp)
                if s:
                    triggered.append("loitering")
                    scores.append(self.cfg.loitering.score)

            if self._zone_polygon is not None:
                s = self._check_restricted_zone(det, w, h)
                if s:
                    triggered.append("restricted_zone")
                    scores.append(self.cfg.restricted_zone.score)

            if self.cfg.running.enabled:
                s = self._check_running(det, timestamp)
                if s:
                    triggered.append("running")
                    scores.append(self.cfg.running.score)

            if self.cfg.fall_detection.enabled:
                s = self._check_fall(det, timestamp)
                if s:
                    triggered.append("fall_detection")
                    scores.append(self.cfg.fall_detection.score)

            if crowd_triggered:
                triggered.append("crowd_density")
                scores.append(self.cfg.crowd_density.score)

            results[det.track_id] = RuleResult(score=max(scores) if scores else 0.0,
                                                 triggered_rules=triggered)

        if self.cfg.fight_detection.enabled:
            self._check_fights(persons, timestamp, results)

        if self.cfg.abandoned_object.enabled:
            self._check_abandoned_objects(persons, objects, timestamp, results)

        return results

    # ------------------------------------------------------------------
    # History bookkeeping
    # ------------------------------------------------------------------
    def _update_histories(self, persons, objects, timestamp):
        for det in persons:
            hist = self._persons[det.track_id]
            c = _centroid(det.bbox)
            if hist.first_seen is None:
                hist.first_seen = timestamp
                hist.anchor = c
            hist.positions.append((timestamp, c[0], c[1]))
            x1, y1, x2, y2 = det.bbox
            hist.bbox_history.append((timestamp, max(1.0, x2 - x1), max(1.0, y2 - y1)))

        for det in objects:
            hist = self._objects[det.track_id]
            c = _centroid(det.bbox)
            if hist.first_seen is None:
                hist.first_seen = timestamp
                hist.anchor = c
            hist.positions.append((timestamp, c[0], c[1]))

    def _forget_stale(self, persons, objects, max_age_seconds: float = 20.0):
        now = time.time()
        live_person_ids = {d.track_id for d in persons}
        live_object_ids = {d.track_id for d in objects}
        for tid in list(self._persons.keys()):
            if tid not in live_person_ids and self._persons[tid].positions and \
                    (now - self._persons[tid].positions[-1][0]) > max_age_seconds:
                del self._persons[tid]
        for tid in list(self._objects.keys()):
            if tid not in live_object_ids and self._objects[tid].positions and \
                    (now - self._objects[tid].positions[-1][0]) > max_age_seconds:
                del self._objects[tid]

    def forget(self, track_id: int):
        self._persons.pop(track_id, None)

    # ------------------------------------------------------------------
    # Individual rule checks
    # ------------------------------------------------------------------
    def _check_loitering(self, det, timestamp: float) -> bool:
        cfg = self.cfg.loitering
        hist = self._persons[det.track_id]
        c = _centroid(det.bbox)

        # if the person has wandered outside the loitering radius, re-anchor
        if hist.anchor is None or _dist(c, hist.anchor) > cfg.radius_px:
            hist.anchor = c
            hist.first_seen = timestamp
            return False

        return (timestamp - hist.first_seen) >= cfg.seconds_threshold

    def _check_restricted_zone(self, det, frame_w: int, frame_h: int) -> bool:
        cx, cy = _centroid(det.bbox)
        norm_point = (cx / max(frame_w, 1), cy / max(frame_h, 1))
        return _point_in_polygon(norm_point, self._zone_polygon)

    def _check_running(self, det, timestamp: float) -> bool:
        cfg = self.cfg.running
        hist = self._persons[det.track_id]
        speed = self._recent_speed(hist, timestamp, window_seconds=0.7)
        return speed is not None and speed >= cfg.speed_px_per_sec_threshold

    def _check_fall(self, det, timestamp: float) -> bool:
        """A fallen person: bbox flips from tall (h > w) to wide (w/h > threshold)
        within a short time window, typically accompanied by a fast downward
        centroid move."""
        cfg = self.cfg.fall_detection
        hist = self._persons[det.track_id]
        x1, y1, x2, y2 = det.bbox
        w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
        aspect = w / h

        if aspect < cfg.aspect_ratio_threshold or len(hist.bbox_history) < 2:
            return False

        # was this person "standing" (tall) recently, before flipping wide?
        was_tall = any((w2 / h2) < 1.0 for _, w2, h2 in list(hist.bbox_history)[:-1])
        drop_speed = self._recent_speed(hist, timestamp, window_seconds=0.7, vertical_only=True)
        return was_tall and (drop_speed is None or drop_speed >= cfg.drop_speed_px_per_sec_threshold * 0.5)

    def _check_fights(self, persons, timestamp: float, results: Dict[int, RuleResult]):
        cfg = self.cfg.fight_detection
        n = len(persons)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = persons[i], persons[j]
                ca, cb = _centroid(a.bbox), _centroid(b.bbox)
                if _dist(ca, cb) > cfg.proximity_px:
                    continue
                speed_a = self._recent_speed(self._persons[a.track_id], timestamp, 0.7) or 0.0
                speed_b = self._recent_speed(self._persons[b.track_id], timestamp, 0.7) or 0.0
                if (speed_a + speed_b) >= cfg.relative_speed_px_per_sec_threshold:
                    for det in (a, b):
                        r = results.get(det.track_id)
                        if r is None:
                            results[det.track_id] = RuleResult(score=cfg.score, triggered_rules=["fight_detection"])
                        elif "fight_detection" not in r.triggered_rules:
                            r.triggered_rules.append("fight_detection")
                            r.score = max(r.score, cfg.score)

    def _check_abandoned_objects(self, persons, objects, timestamp: float, results: Dict[int, RuleResult]):
        cfg = self.cfg.abandoned_object
        for obj in objects:
            hist = self._objects[obj.track_id]
            if hist.first_seen is None:
                continue
            oc = _centroid(obj.bbox)

            # has the object been stationary (hasn't drifted far from its
            # first-seen position) for long enough?
            stationary_for = timestamp - hist.first_seen
            drifted = _dist(oc, hist.anchor) > cfg.unattended_radius_px
            if drifted:
                hist.anchor = oc
                hist.first_seen = timestamp
                continue
            if stationary_for < cfg.stationary_seconds_threshold:
                continue

            # is any person currently near it?
            near_person = any(_dist(oc, _centroid(p.bbox)) <= cfg.unattended_radius_px for p in persons)
            if near_person:
                continue

            # abandoned! flag the nearest person if one is at least in frame,
            # otherwise surface it as a synthetic "object" track so the
            # dashboard/evidence manager still records the event.
            nearest = min(persons, key=lambda p: _dist(oc, _centroid(p.bbox))) if persons else None
            target_id = nearest.track_id if nearest is not None else f"object_{obj.track_id}"
            r = results.get(target_id)
            if r is None:
                results[target_id] = RuleResult(score=cfg.score, triggered_rules=["abandoned_object"])
            elif "abandoned_object" not in r.triggered_rules:
                r.triggered_rules.append("abandoned_object")
                r.score = max(r.score, cfg.score)

    # ------------------------------------------------------------------
    @staticmethod
    def _recent_speed(hist: _TrackHistory, timestamp: float, window_seconds: float,
                       vertical_only: bool = False):
        """Pixels/second the track has moved over the last `window_seconds`."""
        pts = [p for p in hist.positions if (timestamp - p[0]) <= window_seconds]
        if len(pts) < 2:
            return None
        (t0, x0, y0), (t1, x1, y1) = pts[0], pts[-1]
        dt = max(1e-3, t1 - t0)
        if vertical_only:
            return abs(y1 - y0) / dt
        return _dist((x0, y0), (x1, y1)) / dt
