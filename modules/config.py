"""
Loads config.yaml into a simple attribute-accessible object so the rest of the
codebase can do `cfg.detector.confidence_threshold` instead of nested dict lookups.
"""
import yaml
from pathlib import Path


class ConfigNode:
    """Turns a nested dict into nested attribute access (read-only)."""

    def __init__(self, data: dict):
        for key, value in data.items():
            if isinstance(value, dict):
                value = ConfigNode(value)
            setattr(self, key, value)

    def __repr__(self):
        return f"ConfigNode({self.__dict__})"

    def as_dict(self):
        out = {}
        for k, v in self.__dict__.items():
            out[k] = v.as_dict() if isinstance(v, ConfigNode) else v
        return out


def load_config(path: str = None) -> ConfigNode:
    if path is None:
        # default: config.yaml at project root, one level up from this file
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return ConfigNode(raw)


# Convenience singleton — most modules just do `from modules.config import CFG`
CFG = load_config()
