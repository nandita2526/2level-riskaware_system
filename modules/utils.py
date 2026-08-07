"""
Shared helper utilities used across pipeline stages:
  - FrameSequenceBuffer: rolling window of T frames per tracked person
  - PersistenceTracker: fraction of recent frames classified "suspicious" per entity (D_persistence)
  - setup_logger: consistent console + file logging
"""
import logging
import time
from collections import deque, defaultdict
import numpy as np


def setup_logger(name: str = "surveillance", level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # avoid duplicate handlers on re-import
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                             datefmt="%H:%M:%S")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


class FrameSequenceBuffer:
    """
    Maintains a rolling buffer of the last `seq_len` CNN feature vectors
    for each tracked person (keyed by track_id), so Stage 4 (LSTM) always has
    a fixed-length T-frame window to classify.
    """

    def __init__(self, seq_len: int = 16, feature_dim: int = 576):
        self.seq_len = seq_len
        self.feature_dim = feature_dim
        self._buffers = defaultdict(lambda: deque(maxlen=self.seq_len))

    def push(self, track_id: int, feature_vector: np.ndarray):
        self._buffers[track_id].append(feature_vector)

    def is_ready(self, track_id: int) -> bool:
        return len(self._buffers[track_id]) == self.seq_len

    def get_sequence(self, track_id: int) -> np.ndarray:
        """Returns shape (seq_len, feature_dim). Pads with zeros if not yet full."""
        buf = list(self._buffers[track_id])
        if len(buf) < self.seq_len:
            pad = [np.zeros(self.feature_dim, dtype=np.float32)] * (self.seq_len - len(buf))
            buf = pad + buf
        return np.stack(buf, axis=0)

    def active_track_ids(self):
        return list(self._buffers.keys())

    def forget(self, track_id: int):
        self._buffers.pop(track_id, None)


class PersistenceTracker:
    """
    Tracks, for each entity (track_id), the fraction of frames within the last
    `window_seconds` that were classified as "suspicious" (any non-Normal class).
    This implements D_persistence from the risk-scoring formula (Section IV).
    """

    def __init__(self, window_seconds: float = 2.0):
        self.window_seconds = window_seconds
        self._events = defaultdict(lambda: deque())  # track_id -> deque[(timestamp, is_suspicious)]

    def update(self, track_id: int, is_suspicious: bool, timestamp: float = None):
        timestamp = timestamp if timestamp is not None else time.time()
        dq = self._events[track_id]
        dq.append((timestamp, is_suspicious))
        self._trim(track_id, timestamp)

    def _trim(self, track_id: int, now: float):
        dq = self._events[track_id]
        while dq and (now - dq[0][0]) > self.window_seconds:
            dq.popleft()

    def get_persistence(self, track_id: int) -> float:
        dq = self._events[track_id]
        if not dq:
            return 0.0
        suspicious_count = sum(1 for _, s in dq if s)
        return suspicious_count / len(dq)

    def forget(self, track_id: int):
        self._events.pop(track_id, None)


def compute_crowd_density(num_detections: int, frame_area: float, reference_density: float = 0.0008) -> float:
    """
    Normalises the number of detected persons per unit frame-area against a
    reference density, clipped to [0, 1]. `reference_density` (people per pixel)
    is a tunable scene-dependent constant — calibrate against typical footage
    for the deployment site.
    """
    if frame_area <= 0:
        return 0.0
    density = num_detections / frame_area
    normalised = density / reference_density
    return float(np.clip(normalised, 0.0, 1.0))
