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

    def __init__(self, source=0, target_fps: int = None, frame_stride: int = 1):
        self.source = source
        self.target_fps = target_fps or CFG.video.target_fps
        self._min_frame_interval = 1.0 / self.target_fps
        self._last_read_time = 0.0

        # Only every `frame_stride`-th frame is decoded and returned; the
        # frames in between are skipped with cap.grab() (cheap — no JPEG/H264
        # decode), which is what makes offline "Upload Video" analysis fast
        # on CPU. frame_stride=1 (the default, always used for live sources)
        # preserves the original behaviour exactly.
        self.frame_stride = max(1, int(frame_stride))
        self._frame_index = 0  # 1-based index of the last frame returned, in the source video

        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video source: {source}")

        # The source's own native frame rate, used to compute a *video-time*
        # clock (frame_index / native_fps) that is independent of how fast
        # this process actually gets to decode/process frames. This matters
        # a lot for offline "Upload Video" analysis: that mode intentionally
        # runs as fast as the CPU allows (no real-time throttling, see
        # `target_fps`), so wall-clock time.time() elapses at a completely
        # different rate than the video's own timeline. Rules like "loitering
        # > 30 seconds" must be evaluated against video-time, not against
        # however long this machine happened to take to crunch the frames —
        # otherwise a fast upload analysis could finish an entire video
        # before 30 *wall-clock* seconds even elapse, and a genuinely
        # 60-video-second loitering event would never trigger.
        self.native_fps = self.cap.get(cv2.CAP_PROP_FPS) or float(self.target_fps) or 25.0
        if self.native_fps <= 0:
            self.native_fps = float(self.target_fps) or 25.0

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
        self._frame_index += 1

        # Skip (grab-only, no decode) the next (frame_stride - 1) frames so
        # detection/classification only runs on every Nth frame.
        for _ in range(self.frame_stride - 1):
            if not self.cap.grab():
                break
            self._frame_index += 1

        self._last_read_time = time.time()
        video_time = self._frame_index / self.native_fps
        return {
            "raw_frame": raw_frame_bgr,
            "timestamp": time.time(),      # wall-clock — used for real-world alert cooldowns/UI
            "video_time": video_time,       # video-relative clock — used by time-based rules
            "frame_index": self._frame_index,
        }

    def get_fps(self) -> float:
        return self.cap.get(cv2.CAP_PROP_FPS) or self.target_fps

    def release(self):
        if self.cap.isOpened():
            self.cap.release()
