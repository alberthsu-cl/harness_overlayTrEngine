from __future__ import annotations

from pathlib import Path
import json
from typing import Any


EFFECT_CATALOG_VERSION = 1
DEFAULT_EFFECT_CATALOG_RELATIVE_PATH = Path("harness/configs/effect_catalog.json")
DEFAULT_EFFECT_CATALOG_SOURCE_RELATIVE_PATH = Path("harness/configs/effect_catalog_sources.json")


_EFFECT_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "effect_id": "builtin-seamless",
        "mode": "builtin-seamless",
        "effect_source": "builtin",
        "family": "seamless",
        "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "style_hints": ("seamless", "smooth", "generated-seamless"),
        "retrieval_priority": 0,
        "source_documents": (
            "harness/examples/effect_specs/builtin_seamless_sliding.json",
            "harness/examples/render_job.sample.json",
            "harness/examples/render_job.sample.real.json",
        ),
    },
    {
        "effect_id": "builtin-glitch",
        "mode": "builtin-glitch",
        "effect_source": "builtin",
        "family": "glitch",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "style_hints": ("glitch", "generated-glitch"),
        "retrieval_priority": 0,
        "source_documents": (
            "harness/examples/render_job.effect_spec.sample.json",
            "harness/examples/render_job.effect_spec.sample.real.json",
        ),
    },
    {
        "effect_id": "generated-seamless-placeholder",
        "mode": "generated-seamless-placeholder",
        "effect_source": "generated",
        "family": "seamless",
        "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "fallback_fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "style_hints": ("generated-seamless",),
        "retrieval_priority": 10,
        "source_documents": (
            "harness/examples/effect_specs/generated_SeamlessSliding_placeholder.json",
        ),
    },
    {
        "effect_id": "generated-glitch-placeholder",
        "mode": "generated-glitch-placeholder",
        "effect_source": "generated",
        "family": "glitch",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "fallback_fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "style_hints": ("generated-glitch",),
        "retrieval_priority": 10,
        "source_documents": (
            "harness/examples/effect_specs/generated_glitch_placeholder.json",
        ),
    },
)


def build_effect_catalog(repo_root: Path) -> dict[str, Any]:
    source_manifest = _load_effect_catalog_source_manifest(repo_root)
    source_blueprints = (
        source_manifest["registrations"]
        if source_manifest is not None
        else list(_EFFECT_BLUEPRINTS)
    )
    effects = [_build_effect_record(repo_root, blueprint) for blueprint in source_blueprints]
    retrieval_index = {
        style: record["effect_id"]
        for record in effects
        for style in record["style_hints"]
        if record["effect_source"] == "builtin"
    }
    return {
        "catalog_type": "effect_catalog",
        "catalog_version": EFFECT_CATALOG_VERSION,
        "source_root": "harness",
        "effects": effects,
        "retrieval_index": retrieval_index,
    }


def load_effect_catalog(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    if catalog.get("catalog_type") != "effect_catalog":
        raise ValueError(f"effect catalog is invalid: {file_path}")
    return catalog


def select_effect_candidate(catalog: dict[str, Any], style: str, input_kind: str) -> dict[str, Any] | None:
    effects = catalog.get("effects")
    if not isinstance(effects, list):
        return None

    candidates = [
        effect
        for effect in effects
        if isinstance(effect, dict)
        and effect.get("effect_source") == "builtin"
        and style in set(effect.get("style_hints", []))
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda effect: (
            int(effect.get("retrieval_priority", 999)),
            str(effect.get("family", "")),
            str(effect.get("effect_id", "")),
        )
    )
    selected = candidates[0]
    return {
        "effect_id": selected.get("effect_id"),
        "mode": selected.get("mode"),
        "fx_id": selected.get("fx_id"),
        "family": selected.get("family"),
        "style": style,
        "input_kind": input_kind,
        "retrieval_source": "effect_catalog",
        "retrieval_priority": selected.get("retrieval_priority"),
        "source_documents": selected.get("source_documents"),
    }


def _load_effect_catalog_source_manifest(repo_root: Path) -> dict[str, Any] | None:
    source_manifest_path = repo_root / DEFAULT_EFFECT_CATALOG_SOURCE_RELATIVE_PATH
    if not source_manifest_path.exists():
        return None

    with source_manifest_path.open("r", encoding="utf-8") as handle:
        source_manifest = json.load(handle)

    if source_manifest.get("catalog_type") != "effect_catalog_sources":
        raise ValueError(f"effect catalog source manifest is invalid: {source_manifest_path}")

    registrations = source_manifest.get("registrations")
    if not isinstance(registrations, list):
        raise ValueError("effect catalog source manifest registrations must be a list")

    return {
        "catalog_type": source_manifest["catalog_type"],
        "catalog_version": source_manifest.get("catalog_version", 1),
        "registrations": registrations,
    }


def _build_effect_record(repo_root: Path, blueprint: dict[str, Any]) -> dict[str, Any]:
    record = dict(blueprint)
    record["style_hints"] = list(blueprint["style_hints"])
    record["source_documents"] = [
        _format_repo_path((repo_root / relative_path).resolve(), repo_root)
        for relative_path in blueprint["source_documents"]
    ]
    return record


def _format_repo_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)
