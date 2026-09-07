"""
MODULE 2 — PREPROCESSING MODULE
=================================
Resizes each extracted frame to the model's required input resolution and
normalises pixel values to a consistent range for stable deep learning
inference.

(Matches Phase-1 presentation, Slide 21 — Module 2 responsibilities.)
"""
import cv2
import numpy as np

from modules.config import CFG


class PreprocessingModule:
    def __init__(self, frame_size=None, normalize: bool = None):
        self.frame_size = tuple(frame_size or CFG.video.frame_size)
        self.normalize = CFG.video.normalize if normalize is None else normalize

    def process(self, raw_frame_bgr: np.ndarray) -> dict:
        """
        Resizes to the configured (W, H) and optionally normalises to [0, 1].
        Returns both the display-ready BGR frame (uint8, for drawing overlays)
        and the model-ready normalised array (float32, for downstream models).
        """
        resized = cv2.resize(raw_frame_bgr, self.frame_size, interpolation=cv2.INTER_LINEAR)

        model_input = resized.astype(np.float32)
        if self.normalize:
            model_input = model_input / 255.0

        return {
            "display_frame": resized,      # uint8 BGR — for cv2.imshow / drawing overlays / dashboard stream
            "model_input": model_input,     # float32, normalised — for downstream models
        }
