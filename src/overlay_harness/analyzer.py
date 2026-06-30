from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typing import Any

from .planner import auto_styles, build_recommended_plan, infer_input_kind


STYLE_HINTS = set(auto_styles())
ANALYSIS_ARTIFACT_VERSION = 2


METADATA_TRANSITION_FAMILY_TO_STYLE: dict[str, str] = {
    "smooth": "seamless",
    "seamless": "seamless",
    "glitch": "glitch",
    "generated-smooth": "generated-seamless",
    "generated-glitch": "generated-glitch",
}


def analyze_transition(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    input_kind: str,
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    reference_transition: Path | None,
    job_name: str | None,
) -> dict:
    detected_input_kind = input_kind
    if detected_input_kind == "auto":
        detected_input_kind = infer_input_kind(repo_root, source_a, source_b)

    source_a_signals = inspect_prepared_input(source_a)
    source_b_signals = inspect_prepared_input(source_b)
    pair_signals = summarize_pair_signals(source_a_signals, source_b_signals)

    resolved_style_hint, reason = _resolve_style_hint(
        style_hint=style_hint,
        intent=intent,
        prefer_generated=prefer_generated,
        detected_input_kind=detected_input_kind,
        pair_signals=pair_signals,
    )

    notes = f"Analyzer selected '{resolved_style_hint}' because {reason}."
    if intent:
        notes += f" Intent: {intent}"

    return {
        "style_hint": resolved_style_hint,
        "input_kind": detected_input_kind,
        "reference_transition": _format_optional_path(reference_transition, repo_root),
        "job_name": job_name,
        "notes": notes,
        "analysis": {
            "intent": intent,
            "prefer_generated": prefer_generated,
            "style_reason": reason,
            "signals": pair_signals,
        },
    }


def build_transition_analysis_artifact(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    analyzer_inputs: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any]:
    signals = hint.get("analysis", {}).get("signals", {})
    recommended_plan = build_recommended_plan(
        repo_root=repo_root,
        source_a=source_a,
        source_b=source_b,
        hint_data=hint,
    )
    return {
        "artifact_type": "transition_analysis",
        "artifact_version": ANALYSIS_ARTIFACT_VERSION,
        "sources": {
            "source_a": _format_optional_path(source_a, repo_root),
            "source_b": _format_optional_path(source_b, repo_root),
            "reference_transition": hint.get("reference_transition"),
        },
        "facts": {
            "analyzer_inputs": analyzer_inputs,
            "resolved": {
                "style_hint": hint.get("style_hint"),
                "input_kind": hint.get("input_kind"),
                "job_name": hint.get("job_name"),
                "style_reason": hint.get("analysis", {}).get("style_reason"),
            },
            "signals": signals,
            "notes": hint.get("notes"),
        },
        "planning_recommendation": {
            "producer": "deterministic_analyzer",
            "auto": recommended_plan.get("auto"),
            "style": recommended_plan.get("style"),
            "input_kind": recommended_plan.get("input_kind"),
            "preset": recommended_plan.get("preset"),
            "mode": recommended_plan.get("mode"),
            "job_name": hint.get("job_name"),
            "hint": hint,
        },
    }


def load_clip_metadata(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def derive_analyzer_inputs_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    transition_family = metadata.get("transition_family")
    style_hint = METADATA_TRANSITION_FAMILY_TO_STYLE.get(transition_family)
    style_reason = None
    if style_hint is None:
        style_hint, style_reason = _resolve_style_from_metadata_heuristics(metadata)
    else:
        style_reason = f"clip metadata transition_family was '{transition_family}'"

    prefer_generated = bool(metadata.get("prefer_generated"))
    return {
        "input_kind": metadata.get("input_kind") or "auto",
        "style_hint": style_hint,
        "style_reason": style_reason,
        "prefer_generated": prefer_generated,
        "reference_transition": metadata.get("reference_transition"),
        "job_name": metadata.get("job_name"),
        "notes": metadata.get("notes"),
    }


def _resolve_style_hint(
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    detected_input_kind: str,
    pair_signals: dict[str, Any],
) -> tuple[str, str]:
    if style_hint:
        return style_hint, "an explicit style hint was provided"

    normalized_intent = (intent or "").strip().lower()
    if normalized_intent:
        if "generated" in normalized_intent and "glitch" in normalized_intent:
            return "generated-glitch", "the intent mentions generated and glitch"
        if "generated" in normalized_intent and any(
            token in normalized_intent for token in ("smooth", "seamless", "slide", "sliding")
        ):
            return "generated-seamless", "the intent mentions generated and a smooth or sliding transition"
        if "glitch" in normalized_intent:
            if prefer_generated:
                return "generated-glitch", "the intent mentions glitch and generated output was preferred"
            return "glitch", "the intent mentions glitch"
        if any(token in normalized_intent for token in ("smooth", "seamless", "slide", "sliding")):
            if prefer_generated:
                return "generated-seamless", "the intent mentions a smooth or sliding transition and generated output was preferred"
            return "seamless", "the intent mentions a smooth or sliding transition"

    if prefer_generated:
        if pair_signals["combined_visual_energy"] == "high":
            return "generated-glitch", "generated output was preferred and local frame signals indicate high visual energy"
        if detected_input_kind == "fixture":
            return "generated-seamless", "generated output was preferred and fixture inputs are better served by a visible seamless placeholder"
        return "generated-glitch", "generated output was preferred and real or custom inputs default to a glitch placeholder"

    if pair_signals["combined_visual_energy"] == "high" or pair_signals["combined_motion_level"] == "high":
        return "glitch", "local frame signals indicate high motion or visual energy"

    if pair_signals["detected_static_pair"]:
        return "seamless", "local frame signals indicate a static or low-motion pair that fits the smooth baseline"

    return "seamless", "no stronger signal was provided, so the analyzer chose the safest baseline transition"


def _resolve_style_from_metadata_heuristics(metadata: dict[str, Any]) -> tuple[str, str]:
    motion_level = metadata.get("motion_level")
    visual_energy = metadata.get("visual_energy")
    prefer_generated = bool(metadata.get("prefer_generated"))

    if motion_level == "high" or visual_energy == "high":
        if prefer_generated:
            return "generated-glitch", "metadata indicates high motion or visual energy and generated output was preferred"
        return "glitch", "metadata indicates high motion or visual energy"

    if prefer_generated:
        return "generated-seamless", "metadata did not signal a glitch case and generated output was preferred"

    return "seamless", "metadata did not signal a glitch case, so the analyzer chose the smooth baseline"


def inspect_prepared_input(input_dir: Path) -> dict[str, Any]:
    manifest = _load_prepare_manifest(input_dir)
    frame_files = sorted(
        file_path
        for file_path in input_dir.iterdir()
        if file_path.is_file() and file_path.name.startswith("frame_")
    )

    sample_files = _sample_frame_files(frame_files, limit=12)
    file_sizes = [file_path.stat().st_size for file_path in sample_files]
    hashes = [_hash_file(file_path) for file_path in sample_files]

    distinct_hash_count = len(set(hashes))
    distinct_size_count = len(set(file_sizes))
    average_size = int(sum(file_sizes) / len(file_sizes)) if file_sizes else 0
    size_range = (max(file_sizes) - min(file_sizes)) if file_sizes else 0

    return {
        "path": str(input_dir),
        "manifest_mode": manifest.get("mode") if manifest else None,
        "format": manifest.get("format") if manifest else None,
        "frame_count": manifest.get("frame_count") if manifest else len(frame_files),
        "sampled_frame_count": len(sample_files),
        "distinct_hash_count": distinct_hash_count,
        "distinct_size_count": distinct_size_count,
        "average_sample_size": average_size,
        "sample_size_range": size_range,
        "static_sequence": distinct_hash_count <= 1,
        "motion_level": _classify_motion_level(distinct_hash_count, len(sample_files)),
        "visual_energy": _classify_visual_energy(distinct_hash_count, distinct_size_count, size_range, average_size),
    }


def summarize_pair_signals(source_a: dict[str, Any], source_b: dict[str, Any]) -> dict[str, Any]:
    combined_motion_level = _max_level(source_a["motion_level"], source_b["motion_level"])
    combined_visual_energy = _max_level(source_a["visual_energy"], source_b["visual_energy"])
    return {
        "source_a": source_a,
        "source_b": source_b,
        "combined_motion_level": combined_motion_level,
        "combined_visual_energy": combined_visual_energy,
        "detected_static_pair": bool(source_a["static_sequence"] and source_b["static_sequence"]),
    }


def _load_prepare_manifest(input_dir: Path) -> dict[str, Any] | None:
    manifest_file = input_dir / "prepare_video_manifest.json"
    if not manifest_file.exists():
        return None

    with manifest_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sample_frame_files(frame_files: list[Path], limit: int) -> list[Path]:
    if len(frame_files) <= limit:
        return frame_files

    indexes = {round(index * (len(frame_files) - 1) / (limit - 1)) for index in range(limit)}
    return [frame_files[index] for index in sorted(indexes)]


def _hash_file(file_path: Path) -> str:
    digest = hashlib.sha1()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _classify_motion_level(distinct_hash_count: int, sample_count: int) -> str:
    if sample_count <= 1 or distinct_hash_count <= 1:
        return "low"

    diversity_ratio = distinct_hash_count / sample_count
    if diversity_ratio >= 0.8:
        return "high"
    if diversity_ratio >= 0.35:
        return "medium"
    return "low"


def _classify_visual_energy(
    distinct_hash_count: int,
    distinct_size_count: int,
    size_range: int,
    average_size: int,
) -> str:
    if distinct_hash_count <= 1 and distinct_size_count <= 1:
        return "low"

    if average_size > 0 and size_range / average_size >= 0.25:
        return "high"
    if distinct_hash_count >= 6 or distinct_size_count >= 6:
        return "high"
    if distinct_hash_count >= 3 or distinct_size_count >= 3:
        return "medium"
    return "low"


def _max_level(level_a: str, level_b: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    return level_a if rank[level_a] >= rank[level_b] else level_b


def _format_optional_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)