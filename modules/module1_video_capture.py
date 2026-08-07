"""
MODULE 1 — VIDEO CAPTURE MODULE
================================
Opens and manages the live camera stream (webcam / CCTV / RTSP / video file)
using OpenCV. Handles frame extraction at a configurable rate and passes raw
frames into the processing pipeline.

(Matches Phase-1 presentation, Slide 21 — Module 1 responsibilities.)
"""
import time
import cv2

from modules.config import CFG


class VideoCaptureModule:
    """
    Thin wrapper around cv2.VideoCapture that yields raw frames at a throttled
    target FPS, whether the source is a webcam index (0, 1, ...), a video file
    path, or an RTSP/HTTP stream URL.
    """

    def __init__(self, source=0, target_fps: int = None):
        self.source = source
        self.target_fps = target_fps or CFG.video.target_fps
        self._min_frame_interval = 1.0 / self.target_fps
        self._last_read_time = 0.0

        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video source: {source}")

    def __iter__(self):
        return self

    def __next__(self):
        # Throttle to target_fps so we don't over-process faster than the
        # real-time budget requires (important on files, which cv2 reads as
        # fast as it can rather than at their native frame rate).
        now = time.time()
        wait = self._min_frame_interval - (now - self._last_read_time)
        if wait > 0:
            time.sleep(wait)

        ok, raw_frame_bgr = self.cap.read()
        if not ok:
            self.cap.release()
            raise StopIteration

        self._last_read_time = time.time()
        return {"raw_frame": raw_frame_bgr, "timestamp": time.time()}

    def get_fps(self) -> float:
        return self.cap.get(cv2.CAP_PROP_FPS) or self.target_fps

    def release(self):
        if self.cap.isOpened():
            self.cap.release()
