"""
Trains the CNN+LSTM temporal classifier (Stage 4) on pre-extracted feature
sequences, using an 80/10/10 train/val/test split matching Section V of the
paper. Saves the best validation-accuracy checkpoint to
models/best_lstm.pt (path configurable via config.yaml -> lstm.checkpoint_path).
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split
from tqdm import tqdm

from modules.config import CFG
from modules.module4_behaviour_classification import CNNLSTMClassifier
from modules.utils import setup_logger
from training.dataset import FeatureSequenceDataset

logger = setup_logger("train_lstm")


def split_dataset(dataset, train_frac, val_frac, seed):
    n = len(dataset)
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    n_test = n - n_train - n_val
    generator = torch.Generator().manual_seed(seed)
    return random_split(dataset, [n_train, n_val, n_test], generator=generator)


def evaluate(model, loader, device, criterion):
    model.eval()
    total_loss, correct, total = 0.0, 0, 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            logits = model(x)
            loss = criterion(logits, y)
            total_loss += loss.item() * x.size(0)
            preds = torch.argmax(logits, dim=1)
            correct += (preds == y).sum().item()
            total += x.size(0)
    return total_loss / total, correct / total


def main():
    torch.manual_seed(CFG.training.seed)
    device = torch.device(CFG.device)

    dataset = FeatureSequenceDataset()
    train_ds, val_ds, test_ds = split_dataset(
        dataset, CFG.training.train_split, CFG.training.val_split, CFG.training.seed
    )
    logger.info(f"Dataset split -> train: {len(train_ds)}, val: {len(val_ds)}, test: {len(test_ds)}")

    train_loader = DataLoader(train_ds, batch_size=CFG.training.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=CFG.training.batch_size, shuffle=False)

    model = CNNLSTMClassifier().to(device)
    # label_smoothing softens the one-hot targets so the model isn't rewarded
    # for driving predictions to 100% confidence on (easy) training examples —
    # one of the concrete anti-overfitting levers alongside dropout/weight_decay.
    label_smoothing = getattr(CFG.training, "label_smoothing", 0.0)
    criterion = nn.CrossEntropyLoss(label_smoothing=label_smoothing)
    optimizer = torch.optim.Adam(model.parameters(), lr=CFG.training.learning_rate,
                                  weight_decay=CFG.training.weight_decay)

    checkpoint_path = Path(CFG.lstm.checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    early_stopping_patience = getattr(CFG.training, "early_stopping_patience", 0)
    epochs_since_improvement = 0
    best_val_loss = float("inf")
    best_val_acc = 0.0

    for epoch in range(1, CFG.training.epochs + 1):
        model.train()
        running_loss, correct, total = 0.0, 0, 0

        progress = tqdm(train_loader, desc=f"Epoch {epoch}/{CFG.training.epochs}", leave=False)
        for x, y in progress:
            x, y = x.to(device), y.to(device)

            optimizer.zero_grad()
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()

            running_loss += loss.item() * x.size(0)
            preds = torch.argmax(logits, dim=1)
            correct += (preds == y).sum().item()
            total += x.size(0)
            progress.set_postfix(loss=loss.item())

        train_loss = running_loss / total
        train_acc = correct / total
        val_loss, val_acc = evaluate(model, val_loader, device, criterion)

        gap_flag = "  [possible overfitting: train/val accuracy gap > 0.15]" \
            if (train_acc - val_acc) > 0.15 else ""
        logger.info(f"Epoch {epoch:02d} | train_loss={train_loss:.4f} train_acc={train_acc:.4f} "
                    f"| val_loss={val_loss:.4f} val_acc={val_acc:.4f}{gap_flag}")

        if val_loss < best_val_loss - 1e-4:
            best_val_loss = val_loss
            best_val_acc = val_acc
            epochs_since_improvement = 0
            torch.save(model.state_dict(), checkpoint_path)
            logger.info(f"  -> New best val_loss={val_loss:.4f} (val_acc={val_acc:.4f}), "
                        f"checkpoint saved to {checkpoint_path}")
        else:
            epochs_since_improvement += 1
            if early_stopping_patience and epochs_since_improvement >= early_stopping_patience:
                logger.info(f"Early stopping at epoch {epoch} "
                            f"(no val_loss improvement for {early_stopping_patience} epochs).")
                break

    logger.info(f"Training complete. Best val_loss={best_val_loss:.4f}, best val_acc={best_val_acc:.4f}. "
                f"Run training/evaluate.py for full test-set metrics.")


if __name__ == "__main__":
    main()
