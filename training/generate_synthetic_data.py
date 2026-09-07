"""
Generates a synthetic labelled feature-sequence dataset so the CNN+LSTM
classifier (Stage 4) and the whole training/evaluation pipeline can be
exercised end-to-end without waiting on UCF-Crime / DCSASS downloads and
manual clip-level labelling.

Each class has a distinct underlying signal pattern (base feature vector +
class-specific temporal drift + noise), but — unlike a toy generator that
would make every sample of a class an almost-identical copy — this version
adds several sources of realistic variability so the classes are NOT
trivially/perfectly separable:

  1. Per-sample signature jitter    -> no two samples of the same class are
                                        the exact same archetype vector.
  2. Randomised event timing/magnitude -> the "interesting" part of each
                                        behaviour (oscillation, shift, spike)
                                        happens at a different point/strength
                                        each time, not a fixed frame index.
  3. Slow random-walk drift          -> a shared, sequence-level drift term
                                        (camera/lighting/background noise)
                                        that the model must learn to ignore.
  4. Class overlap / hard examples   -> a fraction of samples are generated
                                        as deliberately weak/ambiguous cases
                                        (e.g. a mild, borderline version of a
                                        suspicious pattern, or a "Normal"
                                        sample with a small flicker of
                                        another class' pattern) so accuracy
                                        realistically saturates well below
                                        100%.
  5. Heavier additive Gaussian noise -> approximates sensor/feature noise
                                        from a real CNN feature extractor.

This is for pipeline validation and demonstration, NOT a substitute for real
training data in your final reported results. To train on real data instead,
see README.md Section 9 ("Notes for Your Viva / Demo") and use
modules/module4_behaviour_classification.py::CNNFeatureExtractor over real,
labelled video clips to build a matching data/features/<ClassName>/*.npy set.
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

# Tunable difficulty knobs (fall back to sensible defaults if not present in
# config.yaml, so this script still runs against older configs).
NOISE_STD = getattr(CFG.training, "synthetic_noise_std", 0.4)
SIGNATURE_JITTER_STD = getattr(CFG.training, "synthetic_signature_jitter", 0.35)
DRIFT_STD = getattr(CFG.training, "synthetic_drift_std", 0.05)
# NOTE on why this needs to be real label noise, not just a "weaker" pattern:
# with 576 features x 16 timesteps, even a *faint*-but-consistent per-class
# signal is trivial for an LSTM to pick up with ~perfect accuracy — high
# dimensionality gives huge statistical power. Just turning the intensity
# down still lets the model reach 100%. To get a realistic, non-trivial
# ceiling on accuracy (the way messy real-world behaviour labels do), a
# fraction of samples are generated using a genuinely *different* class'
# pattern while being filed under their original label — true label
# ambiguity that no amount of training can perfectly resolve.
HARD_EXAMPLE_PROB = getattr(CFG.training, "synthetic_hard_example_prob", 0.18)

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "features"


def make_class_signature(class_idx: int) -> np.ndarray:
    """A fixed pseudo-random 'archetype' feature vector per class (the
    class *mean* — individual samples are jittered away from this)."""
    rng = np.random.default_rng(1000 + class_idx)
    return rng.normal(loc=0.0, scale=1.0, size=FEATURE_DIM).astype(np.float32)


def _random_walk_drift(rng: np.random.Generator, seq_len: int, dim: int, std: float) -> np.ndarray:
    """Slow, shared drift across the sequence (simulates lighting/camera/background
    changes) — smooth rather than i.i.d. noise, so it can't just be averaged out."""
    steps = rng.normal(0, std, size=(seq_len, dim)).astype(np.float32)
    return np.cumsum(steps, axis=0)


def _pattern_for(pattern_class_idx: int, rng: np.random.Generator) -> np.ndarray:
    """
    Generates the (SEQ_LEN, FEATURE_DIM) signal pattern for `pattern_class_idx`
    (independent of what label the sample will ultimately be filed under).

      - Normal: stays close to a (jittered) stable baseline throughout.
      - Loitering: baseline with slow oscillation (repeated dwelling), random
        frequency/amplitude per sample.
      - Unattended_Object: baseline for a while, then a shift (object left
        behind) whose onset point and strength vary per sample.
      - Suspicious_Theft_Gesture: baseline with a spike near the end (the
        "gesture"), whose onset and magnitude vary per sample.
    """
    class_name = CLASS_NAMES[pattern_class_idx]

    # Per-sample jittered signature/baseline (same class => different exact
    # vector each time), so the network can't memorise one fixed archetype.
    signature = make_class_signature(pattern_class_idx) + rng.normal(0, SIGNATURE_JITTER_STD, size=FEATURE_DIM).astype(np.float32)
    baseline = make_class_signature(-1) + rng.normal(0, SIGNATURE_JITTER_STD, size=FEATURE_DIM).astype(np.float32)

    # Random event timing / strength per sample.
    osc_freq = rng.uniform(1.2, 3.0)
    osc_amp = rng.uniform(0.35, 0.75)
    shift_onset = rng.uniform(0.35, 0.65)
    shift_strength = rng.uniform(0.5, 1.1)
    spike_onset = rng.uniform(0.6, 0.9)
    spike_strength = rng.uniform(1.4, 2.8)

    drift = _random_walk_drift(rng, SEQ_LEN, FEATURE_DIM, DRIFT_STD)

    seq = np.zeros((SEQ_LEN, FEATURE_DIM), dtype=np.float32)
    for t in range(SEQ_LEN):
        frac = t / (SEQ_LEN - 1)
        noise = rng.normal(0, NOISE_STD, size=FEATURE_DIM).astype(np.float32)

        if class_name == "Normal":
            seq[t] = baseline + noise

        elif class_name == "Loitering":
            oscillation = osc_amp * np.sin(2 * np.pi * osc_freq * frac) * signature
            seq[t] = baseline + oscillation + noise

        elif class_name == "Unattended_Object":
            shift = shift_strength * signature if frac > shift_onset else 0.0
            seq[t] = baseline + shift + noise

        elif class_name == "Suspicious_Theft_Gesture":
            spike = signature * (spike_strength if frac > spike_onset else 0.2)
            seq[t] = baseline + spike + noise

        else:
            seq[t] = baseline + noise

    seq += drift
    return seq.astype(np.float32)


def generate_sequence(class_idx: int, sample_idx: int, force_label_noise: bool = None) -> np.ndarray:
    """
    Builds one (SEQ_LEN, FEATURE_DIM) sequence to be filed under `class_idx`'s
    label. With probability HARD_EXAMPLE_PROB, the sequence is actually
    generated from a *different*, randomly chosen class' pattern (genuine
    label ambiguity/noise) — this is what keeps test accuracy from
    saturating at a trivial 100%, the same way real annotated behaviour
    datasets have some inherently ambiguous/borderline clips.

    A per-sample RNG (seeded from class_idx + sample_idx) is used so the
    dataset is reproducible but every sample is genuinely different — not a
    fixed template plus tiny noise.
    """
    rng = np.random.default_rng(hash((class_idx, sample_idx, CFG.training.seed)) % (2**32))

    is_label_noise = (rng.random() < HARD_EXAMPLE_PROB) if force_label_noise is None else force_label_noise
    if is_label_noise:
        other_classes = [c for c in range(len(CLASS_NAMES)) if c != class_idx]
        pattern_class_idx = int(rng.choice(other_classes))
    else:
        pattern_class_idx = class_idx

    return _pattern_for(pattern_class_idx, rng)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for class_idx, class_name in enumerate(CLASS_NAMES):
        class_dir = OUT_DIR / class_name
        class_dir.mkdir(parents=True, exist_ok=True)
        # remove any stale samples from a previous (easier) generation run
        for stale in class_dir.glob(f"{class_name}_*.npy"):
            stale.unlink()

        for i in range(SAMPLES_PER_CLASS):
            seq = generate_sequence(class_idx, i)
            np.save(class_dir / f"{class_name}_{i:04d}.npy", seq)
            total += 1
        print(f"Generated {SAMPLES_PER_CLASS} samples for class '{class_name}' -> {class_dir}")

    print(f"\nDone. {total} total synthetic sequences written to {OUT_DIR}")
    print(f"(noise_std={NOISE_STD}, signature_jitter={SIGNATURE_JITTER_STD}, "
          f"drift_std={DRIFT_STD}, hard_example_prob={HARD_EXAMPLE_PROB})")
    print("Next: python training/train_lstm.py")


if __name__ == "__main__":
    main()
