from __future__ import annotations

from pathlib import Path
import json

from typing import Any

from .effect_catalog import DEFAULT_EFFECT_CATALOG_RELATIVE_PATH
from .effect_catalog import load_effect_catalog
from .effect_catalog import select_effect_candidate
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
    "builtin-camcorder": {
        "job_name": "planned_camcorder",
        "fx_id": "CES_PlugIn_Camera.dll\\DSP_TR_Camera_Camcorder",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-particle-spray": {
        "job_name": "planned_particle_spray",
        "fx_id": "CES_PlugIn_ParticleSpray.dll\\DSP_TR_Sparkle_01",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-frame-overlay": {
        "job_name": "planned_frame_overlay",
        "fx_id": "CES_PlugIn_FrameOverlay.dll\\DSP_TR_FrameOverlay_01",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur": {
        "job_name": "planned_blur",
        "fx_id": "Blur_DollarBokeh",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-upgrow": {
        "job_name": "planned_blur_upgrow",
        "fx_id": "Blur_UpGrow",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-shakezoom": {
        "job_name": "planned_blur_shakezoom",
        "fx_id": "Blur_ShakeZoom",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-diagblur": {
        "job_name": "planned_blur_diagblur",
        "fx_id": "Blur_DiagBlur",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-hexbokeh": {
        "job_name": "planned_blur_hexbokeh",
        "fx_id": "Blur_HexBokeh",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-diamondbokeh": {
        "job_name": "planned_blur_diamondbokeh",
        "fx_id": "Blur_DiamondBokeh",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-fadeblur": {
        "job_name": "planned_blur_fadeblur",
        "fx_id": "Blur_FadeBlur",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-rotateblur": {
        "job_name": "planned_blur_rotateblur",
        "fx_id": "Blur_RotateBlur",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-blur-dimfade": {
        "job_name": "planned_blur_dimfade",
        "fx_id": "Blur_DimFade",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-ui-snapshot": {
        "job_name": "planned_ui_snapshot",
        "fx_id": "UI_Snapshot",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-ui-app-swipe": {
        "job_name": "planned_ui_app_swipe",
        "fx_id": "UI_AppSwipe",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-ui-rotate-face": {
        "job_name": "planned_ui_rotate_face",
        "fx_id": "UI_RotateFace",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch-hdistortion": {
        "job_name": "planned_glitch_hdistortion",
        "fx_id": "Glitch_HDistor1",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch-stretch-swipe": {
        "job_name": "planned_glitch_stretch_swipe",
        "fx_id": "Glitch_StretchSwipe",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch-hdistortion2": {
        "job_name": "planned_glitch_hdistortion2",
        "fx_id": "Glitch_HDistor2",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch-tunewave": {
        "job_name": "planned_glitch_tunewave",
        "fx_id": "Glitch_TuneWave",
        "effect_spec": None,
        "effect_spec_template": None,
    },
    "builtin-glitch-distortion": {
        "job_name": "planned_glitch_distortion",
        "fx_id": "Glitch_HDistor1",
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
    "camera": "builtin-camcorder",
    "camcorder": "builtin-camcorder",
    "particle": "builtin-particle-spray",
    "sparkle": "builtin-particle-spray",
    "spray": "builtin-particle-spray",
    "frame-overlay": "builtin-frame-overlay",
    "film-roll": "builtin-frame-overlay",
    "overlay": "builtin-frame-overlay",
    "blur": "builtin-blur",
    "bokeh": "builtin-blur",
    "blur-upgrow": "builtin-blur-upgrow",
    "upgrow": "builtin-blur-upgrow",
    "blur-shakezoom": "builtin-blur-shakezoom",
    "shakezoom": "builtin-blur-shakezoom",
    "blur-diagblur": "builtin-blur-diagblur",
    "diagblur": "builtin-blur-diagblur",
    "blur-hexbokeh": "builtin-blur-hexbokeh",
    "hexbokeh": "builtin-blur-hexbokeh",
    "blur-diamondbokeh": "builtin-blur-diamondbokeh",
    "diamondbokeh": "builtin-blur-diamondbokeh",
    "blur-fadeblur": "builtin-blur-fadeblur",
    "fadeblur": "builtin-blur-fadeblur",
    "blur-rotateblur": "builtin-blur-rotateblur",
    "rotateblur": "builtin-blur-rotateblur",
    "blur-dimfade": "builtin-blur-dimfade",
    "dimfade": "builtin-blur-dimfade",
    "ui": "builtin-ui-snapshot",
    "snapshot": "builtin-ui-snapshot",
    "ui-app-swipe": "builtin-ui-app-swipe",
    "app-swipe": "builtin-ui-app-swipe",
    "ui-rotate-face": "builtin-ui-rotate-face",
    "rotate-face": "builtin-ui-rotate-face",
    "glitch-hdistortion": "builtin-glitch-hdistortion",
    "hdistortion": "builtin-glitch-hdistortion",
    "glitch-stretch-swipe": "builtin-glitch-stretch-swipe",
    "stretch-swipe": "builtin-glitch-stretch-swipe",
    "glitch-hdistortion2": "builtin-glitch-hdistortion2",
    "hdistortion2": "builtin-glitch-hdistortion2",
    "glitch-tunewave": "builtin-glitch-tunewave",
    "tunewave": "builtin-glitch-tunewave",
    "distortion": "builtin-glitch-distortion",
    "glitch2": "builtin-glitch-distortion",
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
    planning: dict[str, Any] | None = None,
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
            planning=planning,
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


def load_reference_transition_manifest(reference_path: Path) -> dict[str, Any] | None:
    manifest_path = reference_path
    if reference_path.is_dir():
        manifest_path = reference_path / "reference_transition_manifest.json"

    if not manifest_path.exists():
        return None

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if manifest.get("artifact_type") != "reference_transition":
        raise ValueError(f"reference transition manifest is invalid: {manifest_path}")
    return manifest


def resolve_planned_frame_count(reference_transition: Path | None, explicit_frame_count: int | None) -> tuple[int, str]:
    if explicit_frame_count is not None:
        return explicit_frame_count, "explicit"

    if reference_transition is not None:
        manifest = load_reference_transition_manifest(reference_transition)
        if manifest is not None:
            frame_count = manifest.get("frame_count")
            if not isinstance(frame_count, int) or frame_count < 2:
                raise ValueError("reference transition manifest frame_count must be an integer >= 2")
            return frame_count, "reference_transition_manifest"

    return 30, "default"


def extract_hint_from_analysis(analysis_data: dict[str, Any]) -> dict[str, Any]:
    planning_recommendation = analysis_data.get("planning_recommendation")
    if isinstance(planning_recommendation, dict):
        hint_data = planning_recommendation.get("hint")
        if isinstance(hint_data, dict):
            return hint_data

    hint_data = analysis_data.get("hint")
    if not isinstance(hint_data, dict):
        raise ValueError("analysis artifact does not contain a valid 'hint' object")
    return hint_data


def extract_plan_from_analysis(analysis_data: dict[str, Any]) -> dict[str, Any] | None:
    planning_recommendation = analysis_data.get("planning_recommendation")
    if isinstance(planning_recommendation, dict):
        return planning_recommendation

    recommended_plan = analysis_data.get("recommended_plan")
    if isinstance(recommended_plan, dict):
        return recommended_plan

    return None


def extract_sources_from_analysis(analysis_data: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    sources = analysis_data.get("sources")
    if isinstance(sources, dict):
        source_a = sources.get("source_a")
        source_b = sources.get("source_b")
        reference_transition = sources.get("reference_transition")
        return (
            str(source_a) if source_a is not None else None,
            str(source_b) if source_b is not None else None,
            str(reference_transition) if reference_transition is not None else None,
        )

    source_a = analysis_data.get("source_a")
    source_b = analysis_data.get("source_b")
    reference_transition = analysis_data.get("reference_transition")
    return (
        str(source_a) if source_a is not None else None,
        str(source_b) if source_b is not None else None,
        str(reference_transition) if reference_transition is not None else None,
    )


def extract_resolved_facts_from_analysis(analysis_data: dict[str, Any]) -> dict[str, Any] | None:
    facts = analysis_data.get("facts")
    if isinstance(facts, dict):
        resolved = facts.get("resolved")
        if isinstance(resolved, dict):
            return resolved

    return None


def build_recommended_plan(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    hint_data: dict[str, Any],
) -> dict[str, Any]:
    style = hint_data.get("style_hint")
    input_kind = hint_data.get("input_kind") or "auto"
    if not style:
        raise ValueError("hint data is missing style_hint")

    preset, mode, resolved_input_kind = resolve_auto_plan(
        repo_root=repo_root,
        source_a=source_a,
        source_b=source_b,
        style=str(style),
        input_kind=str(input_kind),
    )
    return {
        "auto": True,
        "style": style,
        "input_kind": resolved_input_kind,
        "preset": preset,
        "mode": mode,
        "retrieval": _build_retrieval_summary(
            repo_root,
            style=str(style),
            input_kind=resolved_input_kind,
            fallback_mode=mode,
            fallback_preset=preset,
        ),
    }


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

    retrieval = _load_effect_catalog(repo_root)
    retrieved_effect = select_effect_candidate(retrieval, style=style, input_kind=resolved_kind) if retrieval else None
    if retrieved_effect is not None:
        mode = str(retrieved_effect["mode"])
        preset = _resolve_preset_for_retrieved_mode(resolved_kind, style, mode, preset)
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


def _load_effect_catalog(repo_root: Path) -> dict[str, Any] | None:
    catalog_path = repo_root / DEFAULT_EFFECT_CATALOG_RELATIVE_PATH
    if not catalog_path.exists():
        return None
    return load_effect_catalog(catalog_path)


def _resolve_preset_for_retrieved_mode(
    resolved_kind: str,
    style: str,
    mode: str,
    existing_preset: str | None,
) -> str | None:
    if mode == "builtin-seamless":
        if resolved_kind == "real":
            return "real-smoke-seamless"
        if resolved_kind == "fixture":
            return "fixture-smoke-seamless"
    if mode == "builtin-glitch" and resolved_kind == "real":
        return "real-smoke-glitch"

    if style in {"generated-seamless", "generated-glitch"} and resolved_kind == "real":
        return "real-smoke-seamless" if mode == "builtin-seamless" else "real-smoke-glitch"

    return existing_preset


def _build_retrieval_summary(
    repo_root: Path,
    style: str,
    input_kind: str,
    fallback_mode: str,
    fallback_preset: str | None,
) -> dict[str, Any] | None:
    catalog = _load_effect_catalog(repo_root)
    if catalog is None:
        return {
            "status": "not_found",
            "style": style,
            "input_kind": input_kind,
            "fallback_used": fallback_mode.endswith("-placeholder"),
            "fallback_mode": fallback_mode,
            "fallback_preset": fallback_preset,
            "fallback_reason": "effect catalog is unavailable",
        }

    retrieved = select_effect_candidate(catalog, style=style, input_kind=input_kind)
    if retrieved is None:
        return {
            "status": "not_found",
            "style": style,
            "input_kind": input_kind,
            "fallback_used": fallback_mode.endswith("-placeholder"),
            "fallback_mode": fallback_mode,
            "fallback_preset": fallback_preset,
            "fallback_reason": "no catalog entry matched the requested style and input kind",
        }
    return {
        "status": "retrieved",
        "style": style,
        "input_kind": input_kind,
        "fallback_used": False,
        "effect_id": retrieved["effect_id"],
        "mode": retrieved["mode"],
        "family": retrieved["family"],
        "fx_id": retrieved["fx_id"],
        "retrieval_source": retrieved["retrieval_source"],
        "source_documents": retrieved["source_documents"],
    }
