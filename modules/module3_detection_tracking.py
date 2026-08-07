"""
MODULE 3 — DETECTION & TRACKING MODULE
========================================
Runs YOLOv8 on each preprocessed frame to identify and localise all persons.
ByteTracker (via Ultralytics' built-in `.track()` API) assigns and maintains
unique IDs across frames so each individual can be tracked continuously, even
through brief occlusion.

Confidence Score = P(Object) x IoU(pred, truth)          [Slide 18]
ByteTracker = Kalman Filter (motion prediction) + Hungarian Algorithm (ID assignment)

(Matches Phase-1 presentation, Slide 18 & 21 — Module 3 responsibilities.)
"""
from dataclasses import dataclass
from typing import List

import numpy as np
from ultralytics import YOLO

from modules.config import CFG


@dataclass
class Detection:
    track_id: int
    bbox: tuple          # (x1, y1, x2, y2) in pixel coords, on the resized frame
    confidence: float
    class_id: int = 0    # COCO class id (0 = person; others = watched objects e.g. backpack)


class DetectionTrackingModule:
    def __init__(self, model_name: str = None, conf_threshold: float = None, device: str = None):
        self.model_name = model_name or CFG.detector.model_name
        self.conf_threshold = conf_threshold or CFG.detector.confidence_threshold
        self.device = device or CFG.device
        # tracker="bytetrack.yaml" -> Ultralytics' built-in ByteTrack implementation
        # (Kalman filter motion prediction + Hungarian algorithm ID assignment)
        self.tracker_config = "bytetrack.yaml"
        self.model = YOLO(self.model_name)

    def detect_and_track(self, frame_bgr: np.ndarray, persist: bool = True) -> List[Detection]:
        """
        Runs YOLOv8 + ByteTrack on a single frame and returns only "person"
        class detections above the confidence threshold, each carrying a
        stable track_id maintained across frames.
        """
        results = self.model.track(
            frame_bgr,
            classes=CFG.detector.classes,
            conf=self.conf_threshold,
            iou=CFG.detector.iou_threshold,
            tracker=self.tracker_config,
            persist=persist,
            device=self.device,
            verbose=False,
        )

        detections = []
        if not results:
            return detections

        result = results[0]
        if result.boxes is None or result.boxes.id is None:
            return detections  # no tracked detections this frame

        boxes = result.boxes.xyxy.cpu().numpy()
        confs = result.boxes.conf.cpu().numpy()
        track_ids = result.boxes.id.cpu().numpy().astype(int)
        cls_ids = (result.boxes.cls.cpu().numpy().astype(int)
                   if result.boxes.cls is not None else np.zeros(len(boxes), dtype=int))

        for bbox, conf, tid, cid in zip(boxes, confs, track_ids, cls_ids):
            x1, y1, x2, y2 = bbox.tolist()
            detections.append(Detection(track_id=int(tid), bbox=(x1, y1, x2, y2),
                                         confidence=float(conf), class_id=int(cid)))

        return detections

    @staticmethod
    def persons(detections: List["Detection"]) -> List["Detection"]:
        """Filters to only person-class (COCO id 0) detections."""
        return [d for d in detections if d.class_id == 0]

    @staticmethod
    def objects(detections: List["Detection"]) -> List["Detection"]:
        """Filters to only watched-object detections (e.g. backpack/handbag/suitcase)."""
        return [d for d in detections if d.class_id != 0]

    @staticmethod
    def crop(frame_bgr: np.ndarray, bbox: tuple) -> np.ndarray:
        """Crops a detection's bounding box out of the frame, clamped to frame bounds."""
        h, w = frame_bgr.shape[:2]
        x1, y1, x2, y2 = bbox
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        if x2 <= x1 or y2 <= y1:
            return np.zeros((1, 1, 3), dtype=frame_bgr.dtype)
        return frame_bgr[y1:y2, x1:x2]
