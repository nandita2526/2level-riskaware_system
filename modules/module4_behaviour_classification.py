"""
MODULE 4 — BEHAVIOUR CLASSIFICATION MODULE
=============================================
CNN extracts per-frame spatial features from cropped person bounding-box
regions (MobileNetV3 backbone). LSTM processes the sequence of features from
the sliding window (T=16 frames) to classify behaviour as Normal or one of
three suspicious categories.

CNN  : Feature Map = ReLU(W . Input + b)                       [Slide 18]
LSTM : h_t = o_t * tanh(C_t)   (forget/input/output gates)       [Slide 19]

Classes: Normal, Loitering, Unattended_Object, Suspicious_Theft_Gesture

(Matches Phase-1 presentation, Slides 18-19 & 22 — Module 4 responsibilities.)
"""
import numpy as np
import cv2
import torch
import torch.nn as nn
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

from modules.config import CFG


# ---------------------------------------------------------------------------
# 4a. CNN — Spatial Feature Extraction
# ---------------------------------------------------------------------------
class CNNFeatureExtractor:
    """Extracts a 576-D spatial feature vector per detected person crop."""

    def __init__(self, device: str = None, dropout: float = None):
        self.device = torch.device(device or CFG.device)
        self.crop_size = tuple(CFG.feature_extractor.crop_size)
        self.feature_dim = CFG.feature_extractor.feature_dim

        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1
        backbone = mobilenet_v3_small(weights=weights)
        # Drop the final classifier head; keep conv features + avgpool ->
        # 576-D vector per crop, matching the paper's transfer-learning spec.
        self.features = backbone.features
        self.avgpool = backbone.avgpool
        self.dropout = nn.Dropout(p=dropout if dropout is not None else CFG.feature_extractor.dropout)
        self.bn = nn.BatchNorm1d(self.feature_dim)

        self.features.eval().to(self.device)
        self.avgpool.to(self.device)
        self.bn.to(self.device)

        self._preprocess_mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self._preprocess_std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

    @torch.no_grad()
    def extract(self, crop_bgr: np.ndarray) -> np.ndarray:
        return self.extract_batch([crop_bgr])[0]

    @torch.no_grad()
    def extract_batch(self, crops_bgr: list) -> np.ndarray:
        """Returns an array of shape (N, 576) for a batch of cropped person images."""
        tensors = [self._to_tensor(c) for c in crops_bgr]
        batch = torch.stack(tensors, dim=0).to(self.device)
        batch = (batch - self._preprocess_mean.to(self.device)) / self._preprocess_std.to(self.device)

        feat_maps = self.features(batch)          # (N, 576, h, w)
        pooled = self.avgpool(feat_maps)           # (N, 576, 1, 1)
        pooled = torch.flatten(pooled, 1)          # (N, 576)

        if pooled.shape[0] > 1:
            pooled = self.bn(pooled)
        pooled = self.dropout(pooled)

        return pooled.cpu().numpy().astype(np.float32)

    def _to_tensor(self, crop_bgr: np.ndarray) -> torch.Tensor:
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        crop_resized = cv2.resize(crop_rgb, self.crop_size, interpolation=cv2.INTER_LINEAR)
        tensor = torch.from_numpy(crop_resized).permute(2, 0, 1).float() / 255.0
        return tensor


# ---------------------------------------------------------------------------
# 4b. LSTM — Temporal Behaviour Classification
# ---------------------------------------------------------------------------
class CNNLSTMClassifier(nn.Module):
    """2-layer, 256-hidden-unit LSTM + FC head over a T=16-frame feature window."""

    def __init__(self, input_dim=None, hidden_dim=None, num_layers=None,
                 num_classes=None, dropout=None):
        super().__init__()
        input_dim = input_dim or CFG.lstm.input_dim
        hidden_dim = hidden_dim or CFG.lstm.hidden_dim
        num_layers = num_layers or CFG.lstm.num_layers
        num_classes = num_classes or CFG.lstm.num_classes
        dropout = dropout if dropout is not None else CFG.lstm.dropout

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, num_classes)

    def forward(self, x):
        """x: (batch, seq_len, input_dim) -> logits (batch, num_classes)"""
        out, (h_n, c_n) = self.lstm(x)
        last_hidden = out[:, -1, :]     # final time-step's hidden state (h_t)
        last_hidden = self.dropout(last_hidden)
        logits = self.fc(last_hidden)
        return logits


class BehaviourClassificationModule:
    """
    Combines the CNN feature extractor + LSTM classifier into the single
    Module 4 interface used by the pipeline: crop in, (class, probs, P_threat) out.
    """

    def __init__(self, checkpoint_path: str = None, device: str = None):
        self.device = torch.device(device or CFG.device)
        self.class_names = list(CFG.lstm.class_names)

        self.cnn = CNNFeatureExtractor(device=str(self.device))
        self.lstm_model = CNNLSTMClassifier().to(self.device)

        checkpoint_path = checkpoint_path or CFG.lstm.checkpoint_path
        try:
            state_dict = torch.load(checkpoint_path, map_location=self.device)
            self.lstm_model.load_state_dict(state_dict)
            self.loaded_pretrained = True
        except FileNotFoundError:
            # No trained checkpoint yet — model runs with random init, useful
            # for wiring/testing the pipeline before training/train_lstm.py has run.
            self.loaded_pretrained = False

        self.lstm_model.eval()

    def extract_features(self, crops_bgr: list) -> np.ndarray:
        """Stage 4a — CNN spatial feature extraction for a batch of person crops."""
        return self.cnn.extract_batch(crops_bgr)

    @torch.no_grad()
    def classify_sequence(self, feature_sequence: np.ndarray):
        """
        Stage 4b — LSTM temporal classification.
        feature_sequence: (seq_len, input_dim) numpy array.
        Returns (predicted_class_name, probs_dict, p_threat).
        """
        x = torch.from_numpy(feature_sequence).float().unsqueeze(0).to(self.device)  # (1, T, D)
        logits = self.lstm_model(x)
        probs = torch.softmax(logits, dim=-1).squeeze(0).cpu().numpy()

        probs_dict = {name: float(p) for name, p in zip(self.class_names, probs)}
        predicted_class = self.class_names[int(np.argmax(probs))]

        # P_threat: max probability across the non-"Normal" (suspicious) classes,
        # used directly by Module 5's risk-scoring formula.
        suspicious_probs = [p for name, p in probs_dict.items() if name != "Normal"]
        p_threat = float(max(suspicious_probs)) if suspicious_probs else 0.0

        return predicted_class, probs_dict, p_threat
