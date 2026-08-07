"""
Evaluates the trained CNN+LSTM checkpoint on the held-out test split.
Reports accuracy, precision, recall, F1 (matching Section V metrics) and
saves a confusion matrix plot to models/confusion_matrix.png.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import torch
from torch.utils.data import DataLoader
import numpy as np
import matplotlib.pyplot as plt
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                              f1_score, confusion_matrix, classification_report)

from modules.config import CFG
from modules.module4_behaviour_classification import CNNLSTMClassifier
from modules.utils import setup_logger
from training.dataset import FeatureSequenceDataset
from training.train_lstm import split_dataset

logger = setup_logger("evaluate")


def main():
    device = torch.device(CFG.device)
    class_names = list(CFG.lstm.class_names)

    dataset = FeatureSequenceDataset()
    _, _, test_ds = split_dataset(dataset, CFG.training.train_split, CFG.training.val_split, CFG.training.seed)
    test_loader = DataLoader(test_ds, batch_size=CFG.training.batch_size, shuffle=False)

    model = CNNLSTMClassifier().to(device)
    checkpoint_path = Path(CFG.lstm.checkpoint_path)
    if not checkpoint_path.exists():
        raise FileNotFoundError(
            f"No checkpoint found at {checkpoint_path}. Run training/train_lstm.py first."
        )
    model.load_state_dict(torch.load(checkpoint_path, map_location=device))
    model.eval()

    all_preds, all_labels = [], []
    with torch.no_grad():
        for x, y in test_loader:
            x = x.to(device)
            logits = model(x)
            preds = torch.argmax(logits, dim=1).cpu().numpy()
            all_preds.extend(preds.tolist())
            all_labels.extend(y.numpy().tolist())

    acc = accuracy_score(all_labels, all_preds)
    precision = precision_score(all_labels, all_preds, average="macro", zero_division=0)
    recall = recall_score(all_labels, all_preds, average="macro", zero_division=0)
    f1 = f1_score(all_labels, all_preds, average="macro", zero_division=0)

    logger.info(f"Test set size: {len(all_labels)}")
    logger.info(f"Accuracy:  {acc*100:.2f}%")
    logger.info(f"Precision: {precision*100:.2f}%")
    logger.info(f"Recall:    {recall*100:.2f}%")
    logger.info(f"F1-score:  {f1*100:.2f}%")
    print("\n" + classification_report(all_labels, all_preds, target_names=class_names, zero_division=0))

    cm = confusion_matrix(all_labels, all_preds)
    _plot_confusion_matrix(cm, class_names)


def _plot_confusion_matrix(cm: np.ndarray, class_names: list):
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(class_names)))
    ax.set_yticks(range(len(class_names)))
    ax.set_xticklabels(class_names, rotation=45, ha="right")
    ax.set_yticklabels(class_names)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("Confusion Matrix — CNN+LSTM Classifier")

    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                     color="white" if cm[i, j] > cm.max() / 2 else "black")

    fig.colorbar(im, ax=ax)
    fig.tight_layout()

    out_path = Path(__file__).resolve().parent.parent / "models" / "confusion_matrix.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print(f"\nConfusion matrix saved to {out_path}")


if __name__ == "__main__":
    main()
