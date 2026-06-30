from __future__ import annotations

from pathlib import Path

from .planner import auto_styles, infer_input_kind


STYLE_HINTS = set(auto_styles())


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

    resolved_style_hint, reason = _resolve_style_hint(
        style_hint=style_hint,
        intent=intent,
        prefer_generated=prefer_generated,
        detected_input_kind=detected_input_kind,
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
        },
    }


def _resolve_style_hint(
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    detected_input_kind: str,
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
        if detected_input_kind == "fixture":
            return "generated-seamless", "generated output was preferred and fixture inputs are better served by a visible seamless placeholder"
        return "generated-glitch", "generated output was preferred and real or custom inputs default to a glitch placeholder"

    return "seamless", "no stronger signal was provided, so the analyzer chose the safest baseline transition"


def _format_optional_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)