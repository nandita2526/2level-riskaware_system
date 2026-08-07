"""
Generates a synthetic labelled feature-sequence dataset so the CNN+LSTM
classifier (Stage 4) and the whole training/evaluation pipeline can be
exercised end-to-end without waiting on UCF-Crime / DCSASS downloads and
manual clip-level labelling.

Each class is given a distinct underlying signal pattern (base feature vector +
class-specific temporal drift + noise) so the LSTM has genuine, learnable
structure to pick up on — this is for pipeline validation and demonstration,
NOT a substitute for real training data in your final reported results.

To train on real data instead, see README.md Section 3 and
src/feature_extractor.py::extract_from_video().
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import numpy as np

from modules.config import CFG

RNG = np.random.default_rng(CFG.training.seed)

CLASS_NAMES = list(CFG.lstm.class_names)
FEATURE_DIM = CFG.lstm.input_dim
SEQ_LEN = CFG.video.sequence_length
SAMPLES_PER_CLASS = CFG.training.synthetic_samples_per_class

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "features"


def make_class_signature(class_idx: int) -> np.ndarray:
    """A fixed pseudo-random 'archetype' feature vector per class."""
    rng = np.random.default_rng(1000 + class_idx)
    return rng.normal(loc=0.0, scale=1.0, size=FEATURE_DIM).astype(np.float32)


def generate_sequence(class_idx: int) -> np.ndarray:
    """
    Builds one (SEQ_LEN, FEATURE_DIM) sequence:
      - Normal: stays close to a stable baseline signature throughout.
      - Loitering: baseline signature with slow oscillation (repeated dwelling).
      - Unattended_Object: baseline for first half, then a sudden shift (object left behind).
      - Suspicious_Theft_Gesture: baseline with a sharp spike near the end (the "gesture").
    """
    signature = make_class_signature(class_idx)
    baseline = make_class_signature(-1)  # shared "normal person walking" signature
    seq = np.zeros((SEQ_LEN, FEATURE_DIM), dtype=np.float32)

    class_name = CLASS_NAMES[class_idx]
    for t in range(SEQ_LEN):
        frac = t / (SEQ_LEN - 1)
        noise = RNG.normal(0, 0.15, size=FEATURE_DIM).astype(np.float32)

        if class_name == "Normal":
            seq[t] = baseline + noise

        elif class_name == "Loitering":
            oscillation = 0.6 * np.sin(2 * np.pi * 2 * frac) * signature
            seq[t] = baseline + oscillation + noise

        elif class_name == "Unattended_Object":
            shift = signature if frac > 0.5 else 0.0
            seq[t] = baseline + shift + noise

        elif class_name == "Suspicious_Theft_Gesture":
            spike = signature * (2.5 if frac > 0.75 else 0.2)
            seq[t] = baseline + spike + noise

        else:
            seq[t] = baseline + noise

    return seq


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for class_idx, class_name in enumerate(CLASS_NAMES):
        class_dir = OUT_DIR / class_name
        class_dir.mkdir(parents=True, exist_ok=True)
        for i in range(SAMPLES_PER_CLASS):
            seq = generate_sequence(class_idx)
            np.save(class_dir / f"{class_name}_{i:04d}.npy", seq)
            total += 1
        print(f"Generated {SAMPLES_PER_CLASS} samples for class '{class_name}' -> {class_dir}")

    print(f"\nDone. {total} total synthetic sequences written to {OUT_DIR}")
    print("Next: python training/train_lstm.py")


if __name__ == "__main__":
    main()
