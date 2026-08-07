"""
PyTorch Dataset for pre-extracted CNN feature sequences.

Expects the directory layout:
    data/features/<ClassName>/*.npy      # each .npy is shape (seq_len, feature_dim)

Works identically whether the .npy files came from generate_synthetic_data.py
or from real UCF-Crime/DCSASS clips processed via
src/feature_extractor.py::extract_from_video().
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import numpy as np
import torch
from torch.utils.data import Dataset

from modules.config import CFG


class FeatureSequenceDataset(Dataset):
    def __init__(self, features_dir: str = None, class_names: list = None):
        self.features_dir = Path(features_dir or (Path(__file__).resolve().parent.parent / "data" / "features"))
        self.class_names = class_names or list(CFG.lstm.class_names)
        self.class_to_idx = {name: idx for idx, name in enumerate(self.class_names)}

        self.samples = []  # list of (filepath, label_idx)
        for class_name in self.class_names:
            class_dir = self.features_dir / class_name
            if not class_dir.exists():
                continue
            for npy_file in sorted(class_dir.glob("*.npy")):
                self.samples.append((npy_file, self.class_to_idx[class_name]))

        if not self.samples:
            raise FileNotFoundError(
                f"No .npy feature files found under {self.features_dir}. "
                "Run training/generate_synthetic_data.py first, or extract real "
                "features via src/feature_extractor.py::extract_from_video()."
            )

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        filepath, label = self.samples[idx]
        sequence = np.load(filepath).astype(np.float32)
        return torch.from_numpy(sequence), label
