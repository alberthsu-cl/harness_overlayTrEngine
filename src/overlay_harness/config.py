from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def load_config(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_allowed_effects(config_dir: Path) -> dict[str, Any]:
    return load_config(config_dir / "allowed_effects.json")


def load_eval_thresholds(config_dir: Path) -> dict[str, Any]:
    return load_config(config_dir / "eval_thresholds.json")