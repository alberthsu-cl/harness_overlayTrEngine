from __future__ import annotations

from pathlib import Path
import json

from typing import Any

from .models import EffectSpec, InputSpec, RenderJob, RenderSettings


PLANNER_MODES: dict[str, dict[str, str | None]] = {
    "builtin-seamless": {
        "job_name": "planned_seamless_sliding",
        "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch": {
        "job_name": "planned_glitch",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "generated-seamless-placeholder": {
        "job_name": "planned_generated_seamless_placeholder",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "effect_spec": "harness/examples/effect_specs/generated_SeamlessSliding_placeholder.json",
        "effect_spec_template": "harness/examples/effect_specs/generated_SeamlessSliding_placeholder.json",
    },
    "generated-glitch-placeholder": {
        "job_name": "planned_generated_glitch_placeholder",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "effect_spec": "harness/examples/effect_specs/generated_glitch_placeholder.json",
        "effect_spec_template": "harness/examples/effect_specs/generated_glitch_placeholder.json",
    },
}


PLANNER_PRESETS: dict[str, dict[str, str | None]] = {
    "real-smoke-seamless": {
        "source_a": "harness/examples/inputs/source_a_real",
        "source_b": "harness/examples/inputs/source_b_real",
        "mode": "builtin-seamless",
        "job_name": "planned_real_smoke_seamless",
        "job_output": "harness/work/planned_real_smoke_seamless.render_job.json",
        "effect_spec_output": None,
    },
    "real-smoke-glitch": {
        "source_a": "harness/examples/inputs/source_a_real",
        "source_b": "harness/examples/inputs/source_b_real",
        "mode": "builtin-glitch",
        "job_name": "planned_real_smoke_glitch",
        "job_output": "harness/work/planned_real_smoke_glitch.render_job.json",
        "effect_spec_output": None,
    },
    "real-smoke-generated-glitch": {
        "source_a": "harness/examples/inputs/source_a_real",
        "source_b": "harness/examples/inputs/source_b_real",
        "mode": "generated-glitch-placeholder",
        "job_name": "planned_real_smoke_generated_glitch",
        "job_output": "harness/work/planned_real_smoke_generated_glitch.render_job.json",
        "effect_spec_output": "harness/work/planned_real_smoke_generated_glitch.effect_spec.json",
    },
    "fixture-smoke-seamless": {
        "source_a": "harness/examples/fixtures/blue_green/source_a",
        "source_b": "harness/examples/fixtures/blue_green/source_b",
        "mode": "builtin-seamless",
        "job_name": "planned_fixture_smoke_seamless",
        "job_output": "harness/work/planned_fixture_smoke_seamless.render_job.json",
        "effect_spec_output": None,
    },
}


PLANNER_PRESET_ALIASES: dict[str, str] = {
    "real-smoke": "real-smoke-seamless",
    "fixture-smoke": "fixture-smoke-seamless",
}


AUTO_STYLE_TO_MODE: dict[str, str] = {
    "seamless": "builtin-seamless",
    "smooth": "builtin-seamless",
    "glitch": "builtin-glitch",
    "generated-seamless": "generated-seamless-placeholder",
    "generated-glitch": "generated-glitch-placeholder",
}


AUTO_KIND_STYLE_TO_PRESET: dict[tuple[str, str], str] = {
    ("real", "seamless"): "real-smoke-seamless",
    ("real", "smooth"): "real-smoke-seamless",
    ("real", "glitch"): "real-smoke-glitch",
    ("real", "generated-glitch"): "real-smoke-generated-glitch",
    ("fixture", "seamless"): "fixture-smoke-seamless",
    ("fixture", "smooth"): "fixture-smoke-seamless",
}


def build_planned_job(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    mode: str,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    output_format: str,
    job_name: str | None,
    reference_transition: Path | None,
    effect_spec_output: Path | None,
) -> tuple[RenderJob, dict | None]:
    mode_config = PLANNER_MODES[mode]
    effect_spec_path = mode_config["effect_spec"]
    effect_spec_payload: dict | None = None

    if effect_spec_output:
        template_path_raw = mode_config["effect_spec_template"]
        if template_path_raw is None:
            raise ValueError(f"mode '{mode}' does not use an effect spec")

        template_path = (repo_root / template_path_raw).resolve()
        with template_path.open("r", encoding="utf-8") as handle:
            effect_spec_payload = json.load(handle)
        effect_spec_path = _format_repo_path(effect_spec_output, repo_root)

    return (
        RenderJob(
            job_name=job_name or str(mode_config["job_name"]),
            effect=EffectSpec(
                fx_id=str(mode_config["fx_id"]),
                category="single_pass",
                effect_spec=effect_spec_path,
                uniforms={"progress": 0.0},
            ),
            inputs=InputSpec(
                source_a=_format_repo_path(source_a, repo_root),
                source_b=_format_repo_path(source_b, repo_root),
                reference_transition=(
                    _format_repo_path(reference_transition, repo_root)
                    if reference_transition is not None
                    else None
                ),
            ),
            render=RenderSettings(
                width=width,
                height=height,
                fps=fps,
                frame_count=frame_count,
                output_format=output_format,
            ),
        ),
        effect_spec_payload,
    )


def planner_modes() -> tuple[str, ...]:
    return tuple(PLANNER_MODES.keys())


def planner_presets() -> tuple[str, ...]:
    return tuple(PLANNER_PRESETS.keys()) + tuple(PLANNER_PRESET_ALIASES.keys())


def planner_preset(name: str) -> dict[str, str | None]:
    resolved_name = PLANNER_PRESET_ALIASES.get(name, name)
    return dict(PLANNER_PRESETS[resolved_name])


def load_transition_hint(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_transition_analysis(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def extract_hint_from_analysis(analysis_data: dict[str, Any]) -> dict[str, Any]:
    hint_data = analysis_data.get("hint")
    if not isinstance(hint_data, dict):
        raise ValueError("analysis artifact does not contain a valid 'hint' object")
    return hint_data


def auto_styles() -> tuple[str, ...]:
    return tuple(AUTO_STYLE_TO_MODE.keys())


def auto_input_kinds() -> tuple[str, ...]:
    return ("auto", "real", "fixture", "custom")


def resolve_auto_plan(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    style: str,
    input_kind: str,
) -> tuple[str | None, str, str]:
    resolved_kind = input_kind
    if resolved_kind == "auto":
        resolved_kind = infer_input_kind(repo_root, source_a, source_b)

    preset = AUTO_KIND_STYLE_TO_PRESET.get((resolved_kind, style))
    mode = AUTO_STYLE_TO_MODE[style]
    return preset, mode, resolved_kind


def infer_input_kind(repo_root: Path, source_a: Path, source_b: Path) -> str:
    relative_a = _try_relative_repo_path(source_a, repo_root)
    relative_b = _try_relative_repo_path(source_b, repo_root)

    if relative_a == Path("harness/examples/inputs/source_a_real") and relative_b == Path(
        "harness/examples/inputs/source_b_real"
    ):
        return "real"

    if relative_a == Path("harness/examples/fixtures/blue_green/source_a") and relative_b == Path(
        "harness/examples/fixtures/blue_green/source_b"
    ):
        return "fixture"

    return "custom"


def _format_repo_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


def _try_relative_repo_path(path: Path, repo_root: Path) -> Path | None:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root)
    except ValueError:
        return None