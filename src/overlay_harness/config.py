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
        return _validate_analysis_provider_config(config)

    config_path = config_dir / "analysis_provider.json"
    if not config_path.exists():
        return None
    config = load_config(config_path)
    if not isinstance(config, dict):
        raise ValueError("analysis_provider.json must contain a JSON object")
    config["config_path"] = str(config_path)
    config["config_source"] = "repo:configs/analysis_provider.json"
    return _validate_analysis_provider_config(config)


def _validate_analysis_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("config_type") != "analysis_provider_config":
        raise ValueError("analysis provider config must declare config_type='analysis_provider_config'")

    config_version = config.get("config_version")
    if not isinstance(config_version, int) or config_version < 1:
        raise ValueError("analysis provider config must declare a positive integer config_version")

    default_provider = config.get("default_provider")
    model_backed_provider = config.get("model_backed_provider")
    if not isinstance(default_provider, dict):
        raise ValueError("analysis provider config must include a default_provider object")
    if not isinstance(model_backed_provider, dict):
        raise ValueError("analysis provider config must include a model_backed_provider object")

    _validate_provider_block(default_provider, "default_provider")
    _validate_provider_block(model_backed_provider, "model_backed_provider")

    enabled = model_backed_provider.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("analysis provider config model_backed_provider.enabled must be a boolean")

    return config


def _validate_provider_block(provider: dict[str, Any], label: str) -> None:
    for field_name in ("kind", "name", "mode"):
        value = provider.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"analysis provider config {label}.{field_name} must be a non-empty string")
