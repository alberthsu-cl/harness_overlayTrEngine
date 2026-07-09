from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import json

ANALYSIS_PROVIDER_CONFIG_ENV_VAR = "HARNESS_ANALYSIS_PROVIDER_CONFIG"


def load_config(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_allowed_effects(config_dir: Path) -> dict[str, Any]:
    return load_config(config_dir / "allowed_effects.json")


def load_eval_thresholds(config_dir: Path) -> dict[str, Any]:
    return load_config(config_dir / "eval_thresholds.json")


def load_analysis_provider_config(config_dir: Path) -> dict[str, Any] | None:
    env_value = os.environ.get(ANALYSIS_PROVIDER_CONFIG_ENV_VAR)
    if env_value:
        config_path = Path(env_value).expanduser()
        if not config_path.exists():
            raise FileNotFoundError(
                f"{ANALYSIS_PROVIDER_CONFIG_ENV_VAR} points to missing file: {config_path}"
            )
        config = load_config(config_path)
        if not isinstance(config, dict):
            raise ValueError("analysis provider config from environment must contain a JSON object")
        config["config_path"] = str(config_path)
        config["config_source"] = f"env:{ANALYSIS_PROVIDER_CONFIG_ENV_VAR}"
        return config

    config_path = config_dir / "analysis_provider.json"
    if not config_path.exists():
        return None
    config = load_config(config_path)
    if not isinstance(config, dict):
        raise ValueError("analysis_provider.json must contain a JSON object")
    config["config_path"] = str(config_path)
    config["config_source"] = "repo:configs/analysis_provider.json"
    return config
