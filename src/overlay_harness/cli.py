from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import re
import shutil
from pathlib import Path
import sys
from uuid import uuid4

from .config import load_allowed_effects, load_analysis_provider_config, load_eval_thresholds
from .effect_catalog import build_effect_catalog
from .effect_catalog import build_effect_catalog_audit
from .effect_catalog import load_effect_catalog
from .effect_catalog import select_effect_candidate
from .evaluator import score_frame_sequences
from .models import load_render_job
from .analyzer import ANALYSIS_ENGINE
from .analyzer import analyze_transition
from .analyzer import analyze_transition_video
from .analyzer import build_transition_analysis_artifact, derive_analyzer_inputs_from_metadata, load_clip_metadata
from .planner import (
    auto_input_kinds,
    auto_styles,
    build_recommended_plan,
    build_planned_job,
    extract_plan_from_analysis,
    extract_resolved_facts_from_analysis,
    extract_sources_from_analysis,
    extract_hint_from_analysis,
    load_reference_transition_manifest,
    load_transition_analysis,
    load_transition_hint,
    PLANNER_MODES,
    planner_modes,
    planner_preset,
    planner_presets,
    resolve_planned_frame_count,
    resolve_auto_plan,
)
from .renderer import encode_render_demo_video
from .renderer import prepare_render_invocation
from .report import HarnessReport
from .models import EffectSpec
from .models import InputSpec
from .models import RenderJob
from .models import RenderSettings
from .validator import validate_job
from .video_prep import (
    extract_video_frames,
    prepare_reference_transition,
    prepare_solid_color_frames,
)
from .workspace import create_job_workspace, write_json


OFFICIAL_SMOKE_TEST_JOBS = (
    "harness/examples/render_job.sample.json",
    "harness/examples/render_job.effect_spec.sample.json",
)

OFFICIAL_REAL_SMOKE_TEST_JOBS = (
    "harness/examples/render_job.sample.real.json",
    "harness/examples/render_job.effect_spec.sample.real.json",
)

DEFAULT_RENDERER_RELATIVE_PATH = Path(
    "harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe"
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    harness_root = repo_root / "harness"
    config_dir = harness_root / "configs"
    default_renderer = _resolve_default_renderer(repo_root)

    if args.command == "prepare-video":
        return _handle_prepare_video(args, repo_root)
    if args.command == "prepare-pair":
        return _handle_prepare_pair(args, repo_root)
    if args.command == "prepare-reference-transition":
        return _handle_prepare_reference_transition(args, repo_root)
    if args.command == "analyze-transition":
        return _handle_analyze_transition(args, repo_root)
    if args.command == "analyze-sample-video":
        return _handle_analyze_sample_video(args, repo_root)
    if args.command == "flow":
        return _handle_flow(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "sample-video":
        return _handle_sample_video(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "plan-job":
        return _handle_plan_job(args, repo_root, config_dir)
    if args.command == "smoke-test":
        return _handle_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "real-smoke-test":
        return _handle_real_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "score":
        return _handle_score(args, repo_root)
    if args.command == "index-effects":
        return _handle_index_effects(args, repo_root)
    if args.command == "audit-effects":
        return _handle_audit_effects(args, repo_root)

    result = _execute_job_command(
        repo_root=repo_root,
        harness_root=harness_root,
        config_dir=config_dir,
        job_path=Path(args.job).resolve(),
        command_name=args.command,
        renderer=_resolve_renderer_argument(getattr(args, "renderer", None), default_renderer),
    )

    if args.command == "validate":
        return result["exit_code"]

    if args.command == "prepare":
        print(f"Prepared workspace: {result['workspace']}")
        return result["exit_code"]

    print(json.dumps(result, indent=2))
    return result["exit_code"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Overlay transition harness scaffold")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("validate", "prepare", "run"):
        command = subparsers.add_parser(command_name)
        command.add_argument("--job", required=True, help="Path to a render job JSON file")
        if command_name == "run":
            command.add_argument(
                "--renderer",
                required=False,
                help=(
                    "Path to the headless renderer executable; defaults to "
                    "harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe "
                    "when that file exists"
                ),
            )

    prepare_video = subparsers.add_parser(
        "prepare-video",
        help="Generate test frame sequences or extract frames from a source video",
    )
    prepare_video_mode = prepare_video.add_mutually_exclusive_group(required=True)
    prepare_video_mode.add_argument(
        "--solid-color",
        help="Generate a solid-color frame sequence using a named color, #RRGGBB, or R,G,B",
    )
    prepare_video_mode.add_argument(
        "--source-video",
        help="Extract frames from a video file using ffmpeg",
    )
    prepare_video.add_argument("--output-dir", required=True, help="Directory for generated or extracted frames")
    prepare_video.add_argument("--width", type=int, default=1920, help="Target output width")
    prepare_video.add_argument("--height", type=int, default=1080, help="Target output height")
    prepare_video.add_argument("--fps", type=int, default=30, help="Frame rate for extraction or fixture metadata")
    prepare_video.add_argument(
        "--frame-count",
        type=int,
        default=None,
        help="Frame count for solid-color generation or an optional cap for video extraction",
    )
    prepare_video.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg for video extraction mode")

    prepare_pair = subparsers.add_parser(
        "prepare-pair",
        help="Generate a paired A/B fixture set in one command",
    )
    prepare_pair.add_argument("--output-root", required=True, help="Root directory that will contain source_a and source_b")
    prepare_pair.add_argument("--color-a", default="blue", help="Fixture color for source A")
    prepare_pair.add_argument("--color-b", default="green", help="Fixture color for source B")
    prepare_pair.add_argument("--width", type=int, default=1920, help="Target output width")
    prepare_pair.add_argument("--height", type=int, default=1080, help="Target output height")
    prepare_pair.add_argument("--fps", type=int, default=30, help="Frame rate metadata for the fixture manifests")
    prepare_pair.add_argument("--frame-count", type=int, default=30, help="Frame count for both fixture sequences")

    prepare_reference_transition_cmd = subparsers.add_parser(
        "prepare-reference-transition",
        help="Detect and normalize a transition segment from a sample transition video",
    )
    prepare_reference_transition_cmd.add_argument(
        "--source-video",
        required=True,
        help="Path to the sample transition video",
    )
    prepare_reference_transition_cmd.add_argument(
        "--output-dir",
        required=True,
        help="Directory for the normalized reference transition frames and manifest",
    )
    prepare_reference_transition_cmd.add_argument("--width", type=int, default=1920, help="Target output width")
    prepare_reference_transition_cmd.add_argument("--height", type=int, default=1080, help="Target output height")
    prepare_reference_transition_cmd.add_argument("--fps", type=int, default=30, help="Normalization frame rate")
    prepare_reference_transition_cmd.add_argument(
        "--target-frame-count",
        type=int,
        default=30,
        help="Exact number of normalized reference frames to produce",
    )
    prepare_reference_transition_cmd.add_argument(
        "--analysis-width",
        type=int,
        default=64,
        help="Low-resolution analysis width for transition detection",
    )
    prepare_reference_transition_cmd.add_argument(
        "--analysis-height",
        type=int,
        default=36,
        help="Low-resolution analysis height for transition detection",
    )
    prepare_reference_transition_cmd.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg")

    analyze_transition_cmd = subparsers.add_parser(
        "analyze-transition",
        help="Create a transition hint JSON from prepared inputs and simple intent heuristics",
    )
    analyze_transition_cmd.add_argument("--source-a", required=True, help="Path to the prepared source A frames")
    analyze_transition_cmd.add_argument("--source-b", required=True, help="Path to the prepared source B frames")
    analyze_transition_cmd.add_argument("--hint-output", required=True, help="Output path for the generated transition hint JSON")
    analyze_transition_cmd.add_argument(
        "--analysis-output",
        required=False,
        help="Optional output path for a richer transition analysis artifact; defaults next to the hint output",
    )
    analyze_transition_cmd.add_argument(
        "--comparison-output",
        required=False,
        help="Optional output path for a JSON audit report that compares the embedded recommendation with a fresh recompute from the analysis facts",
    )
    analyze_transition_cmd.add_argument(
        "--clip-metadata-file",
        required=False,
        help="Optional clip metadata JSON file that the analyzer can use to derive style and other hint fields",
    )
    analyze_transition_cmd.add_argument(
        "--style-hint",
        required=False,
        choices=auto_styles(),
        help="Optional explicit style hint to record directly",
    )
    analyze_transition_cmd.add_argument(
        "--intent",
        required=False,
        help="Optional freeform intent text used by the deterministic analyzer heuristics",
    )
    analyze_transition_cmd.add_argument(
        "--prefer-generated",
        action="store_true",
        help="Bias the analyzer toward generated-placeholder styles when intent is ambiguous",
    )
    analyze_transition_cmd.add_argument(
        "--analysis-provider-kind",
        required=False,
        default="deterministic_rules",
        choices=("deterministic_rules", "model_backed"),
        help="Requested analysis provider kind to record in the transition analysis artifact",
    )
    analyze_transition_cmd.add_argument(
        "--analysis-provider-name",
        required=False,
        help="Optional provider name to record in the transition analysis artifact",
    )
    analyze_transition_cmd.add_argument(
        "--analysis-provider-mode",
        required=False,
        default="deterministic",
        help="Requested provider mode to record in the transition analysis artifact",
    )
    analyze_transition_cmd.add_argument(
        "--input-kind",
        required=False,
        default="auto",
        choices=auto_input_kinds(),
        help="Input kind hint for the analyzer; defaults to auto detection",
    )
    analyze_transition_cmd.add_argument(
        "--reference-transition",
        required=False,
        help="Optional reference transition path to record in the generated hint file",
    )
    analyze_transition_cmd.add_argument("--job-name", required=False, help="Optional job_name hint for downstream planning")

    flow_cmd = subparsers.add_parser(
        "flow",
        help="Run the end-to-end transition flow from a video and prepared source A/B inputs",
    )
    flow_cmd.add_argument("--transition-video", required=True, help="Path to the sample transition video")
    flow_cmd.add_argument("--source-a", required=True, help="Path to the prepared source A frames")
    flow_cmd.add_argument("--source-b", required=True, help="Path to the prepared source B frames")
    flow_cmd.add_argument("--output-root", required=True, help="Directory that will contain the flow artifacts and report")
    flow_cmd.add_argument(
        "--renderer",
        required=False,
        help=(
            "Path to the headless renderer executable; defaults to "
            "harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe "
            "when that file exists"
        ),
    )
    flow_cmd.add_argument(
        "--style-hint",
        required=False,
        choices=auto_styles(),
        help="Optional explicit style hint for the transition analysis",
    )
    flow_cmd.add_argument(
        "--intent",
        required=False,
        help="Optional freeform intent text used by the deterministic analyzer heuristics",
    )
    flow_cmd.add_argument(
        "--prefer-generated",
        action="store_true",
        help="Bias the analyzer toward generated-placeholder styles when intent is ambiguous",
    )
    flow_cmd.add_argument(
        "--analysis-provider-kind",
        required=False,
        default="deterministic_rules",
        choices=("deterministic_rules", "model_backed"),
        help="Requested analysis provider kind to record in the transition analysis artifact",
    )
    flow_cmd.add_argument(
        "--analysis-provider-name",
        required=False,
        help="Optional provider name to record in the transition analysis artifact",
    )
    flow_cmd.add_argument(
        "--analysis-provider-mode",
        required=False,
        default="deterministic",
        help="Requested provider mode to record in the transition analysis artifact",
    )
    flow_cmd.add_argument(
        "--input-kind",
        required=False,
        default="auto",
        choices=auto_input_kinds(),
        help="Input kind hint for the analyzer; defaults to auto detection",
    )
    flow_cmd.add_argument("--job-name", required=False, help="Optional job_name hint for downstream planning")
    flow_cmd.add_argument("--width", type=int, default=1920, help="Target render width")
    flow_cmd.add_argument("--height", type=int, default=1080, help="Target render height")
    flow_cmd.add_argument("--fps", type=int, default=30, help="Target frame rate for analysis and render")
    flow_cmd.add_argument(
        "--frame-count",
        type=int,
        default=None,
        help="Target render frame count; defaults to the prepared reference manifest count when available, otherwise 30",
    )
    flow_cmd.add_argument(
        "--target-frame-count",
        type=int,
        default=30,
        help="Exact number of normalized reference frames to produce from the transition video",
    )
    flow_cmd.add_argument(
        "--analysis-width",
        type=int,
        default=64,
        help="Low-resolution analysis width for transition detection",
    )
    flow_cmd.add_argument(
        "--analysis-height",
        type=int,
        default=36,
        help="Low-resolution analysis height for transition detection",
    )
    flow_cmd.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg")
    flow_cmd.add_argument(
        "--effect-spec-output",
        required=False,
        help="Optional output path for a copied effect_spec template when the chosen mode uses a generated placeholder",
    )

    analyze_sample_video_cmd = subparsers.add_parser(
        "analyze-sample-video",
        help="Normalize a sample transition video and emit the deterministic analysis artifact",
    )
    analyze_sample_video_cmd.add_argument("--transition-video", required=True, help="Path to the sample transition video")
    analyze_sample_video_cmd.add_argument("--source-a", required=True, help="Path to the prepared source A frames")
    analyze_sample_video_cmd.add_argument("--source-b", required=True, help="Path to the prepared source B frames")
    analyze_sample_video_cmd.add_argument("--output-root", required=True, help="Directory that will contain the analysis artifacts")
    analyze_sample_video_cmd.add_argument(
        "--style-hint",
        required=False,
        choices=auto_styles(),
        help="Optional explicit style hint for the transition analysis",
    )
    analyze_sample_video_cmd.add_argument(
        "--intent",
        required=False,
        help="Optional freeform intent text used by the deterministic analyzer heuristics",
    )
    analyze_sample_video_cmd.add_argument(
        "--prefer-generated",
        action="store_true",
        help="Bias the analyzer toward generated-placeholder styles when intent is ambiguous",
    )
    analyze_sample_video_cmd.add_argument(
        "--input-kind",
        required=False,
        default="auto",
        choices=auto_input_kinds(),
        help="Input kind hint for the analyzer; defaults to auto detection",
    )
    analyze_sample_video_cmd.add_argument("--job-name", required=False, help="Optional job_name hint for downstream planning")
    analyze_sample_video_cmd.add_argument("--width", type=int, default=1920, help="Target output width")
    analyze_sample_video_cmd.add_argument("--height", type=int, default=1080, help="Target output height")
    analyze_sample_video_cmd.add_argument("--fps", type=int, default=30, help="Target frame rate for analysis")
    analyze_sample_video_cmd.add_argument(
        "--target-frame-count",
        type=int,
        default=30,
        help="Exact number of normalized reference frames to produce from the transition video",
    )
    analyze_sample_video_cmd.add_argument(
        "--analysis-width",
        type=int,
        default=64,
        help="Low-resolution analysis width for transition detection",
    )
    analyze_sample_video_cmd.add_argument(
        "--analysis-height",
        type=int,
        default=36,
        help="Low-resolution analysis height for transition detection",
    )
    analyze_sample_video_cmd.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg")
    analyze_sample_video_cmd.add_argument(
        "--analysis-output",
        required=False,
        help="Optional output path for the richer transition analysis artifact; defaults under the output root",
    )
    analyze_sample_video_cmd.add_argument(
        "--comparison-output",
        required=False,
        help="Optional output path for a plan-comparison audit report",
    )

    sample_video_cmd = subparsers.add_parser(
        "sample-video",
        help="Render a reference MP4 from A/B inputs using an explicit fx_id, a style hint, or a forced planner mode",
    )
    sample_video_cmd.add_argument("--source-a", required=True, help="Path to the prepared source A frames")
    sample_video_cmd.add_argument("--source-b", required=True, help="Path to the prepared source B frames")
    sample_video_cmd.add_argument("--output-video", required=True, help="Path for the final sample MP4")
    sample_video_cmd.add_argument(
        "--fx-id",
        required=False,
        help="Optional explicit fx_id to render; when omitted, the command uses the current A/B-driven planner",
    )
    sample_video_cmd.add_argument(
        "--style",
        required=False,
        choices=auto_styles(),
        help="Optional style hint when no explicit fx_id is provided",
    )
    sample_video_cmd.add_argument(
        "--force-mode",
        required=False,
        choices=planner_modes(),
        help="Optional planner mode to force when generating the synthetic sample video",
    )
    sample_video_cmd.add_argument(
        "--output-root",
        required=False,
        help="Root directory for sample-video intermediate artifacts; defaults to harness/work/tests",
    )
    sample_video_cmd.add_argument("--job-name", required=False, help="Optional job_name override")
    sample_video_cmd.add_argument("--width", type=int, default=1920, help="Target render width")
    sample_video_cmd.add_argument("--height", type=int, default=1080, help="Target render height")
    sample_video_cmd.add_argument("--fps", type=int, default=30, help="Target frame rate for the sample video")
    sample_video_cmd.add_argument(
        "--frame-count",
        type=int,
        default=30,
        help="Target render frame count for the sample video",
    )
    sample_video_cmd.add_argument(
        "--renderer",
        required=False,
        help=(
            "Path to the headless renderer executable; defaults to "
            "harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe "
            "when that file exists"
        ),
    )
    sample_video_cmd.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg for MP4 encoding")

    index_effects_cmd = subparsers.add_parser(
        "index-effects",
        help="Build the deterministic built-in effect catalog used for retrieval before generation",
    )
    index_effects_cmd.add_argument(
        "--output",
        required=False,
        help=(
            "Output path for the effect catalog JSON; defaults to "
            "harness/configs/effect_catalog.json"
        ),
    )
    index_effects_cmd.add_argument(
        "--source-manifest",
        required=False,
        help=(
            "Optional source manifest JSON; defaults to "
            "harness/configs/effect_catalog_sources.json"
        ),
    )

    audit_effects_cmd = subparsers.add_parser(
        "audit-effects",
        help="Compare the checked-in source manifest against the built-in effect baseline",
    )
    audit_effects_cmd.add_argument(
        "--output",
        required=False,
        help="Optional output path for the audit report JSON",
    )
    audit_effects_cmd.add_argument(
        "--source-manifest",
        required=False,
        help=(
            "Optional source manifest JSON; defaults to "
            "harness/configs/effect_catalog_sources.json"
        ),
    )

    plan_job = subparsers.add_parser(
        "plan-job",
        help="Create a render job from prepared inputs using a rule-based effect mode",
    )
    plan_job.add_argument(
        "--preset",
        required=False,
        choices=planner_presets(),
        help="Optional shortcut for a common plan-job workflow",
    )
    plan_job.add_argument(
        "--hint-file",
        required=False,
        help="Optional transition hint JSON file that provides preset/style/input-kind/reference metadata",
    )
    plan_job.add_argument(
        "--analysis-file",
        required=False,
        help="Optional richer transition analysis JSON file; plan-job derives the planner hint from its embedded hint object",
    )
    plan_job.add_argument(
        "--comparison-output",
        required=False,
        help="Optional output path for a JSON plan comparison report when planning from an analysis artifact",
    )
    plan_job.add_argument(
        "--recompute-plan-from-facts",
        action="store_true",
        help="When using --analysis-file, ignore the embedded planning recommendation and recompute a fresh one from the analysis facts",
    )
    plan_job.add_argument(
        "--auto",
        action="store_true",
        help="Automatically choose a planner preset or mode from input-kind and style hints",
    )
    plan_job.add_argument("--source-a", required=False, help="Path to the prepared source A frames")
    plan_job.add_argument("--source-b", required=False, help="Path to the prepared source B frames")
    plan_job.add_argument("--job-output", required=False, help="Output path for the planned render job JSON")
    plan_job.add_argument(
        "--mode",
        required=False,
        choices=planner_modes(),
        help="Planner effect mode",
    )
    plan_job.add_argument("--job-name", required=False, help="Optional explicit job_name override")
    plan_job.add_argument(
        "--style",
        required=False,
        choices=auto_styles(),
        help="High-level style hint for --auto planning",
    )
    plan_job.add_argument(
        "--input-kind",
        required=False,
        default="auto",
        choices=auto_input_kinds(),
        help="Input kind hint for --auto planning; defaults to auto detection",
    )
    plan_job.add_argument(
        "--effect-spec-output",
        required=False,
        help="Optional output path for a copied effect_spec template when using a generated-placeholder mode",
    )
    plan_job.add_argument(
        "--reference-transition",
        required=False,
        help="Optional reference transition path to store in the planned job",
    )
    plan_job.add_argument("--width", type=int, default=1920, help="Target output width")
    plan_job.add_argument("--height", type=int, default=1080, help="Target output height")
    plan_job.add_argument("--fps", type=int, default=30, help="Target render fps")
    plan_job.add_argument(
        "--frame-count",
        type=int,
        default=None,
        help="Target render frame count; defaults to the prepared reference manifest count when available, otherwise 30",
    )
    plan_job.add_argument(
        "--output-format",
        default="png_sequence",
        help="Target output format; the current scaffold supports png_sequence",
    )

    smoke_test = subparsers.add_parser(
        "smoke-test",
        help="Run the two official current-phase smoke-test jobs",
    )
    smoke_test.add_argument(
        "--renderer",
        required=False,
        help=(
            "Optional path to the native renderer executable for full render smoke tests; "
            "defaults to harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe "
            "when that file exists"
        ),
    )

    real_smoke_test = subparsers.add_parser(
        "real-smoke-test",
        help="Run the two official real-video smoke-test jobs",
    )
    real_smoke_test.add_argument(
        "--renderer",
        required=False,
        help=(
            "Optional path to the native renderer executable for full real-video smoke tests; "
            "defaults to harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe "
            "when that file exists"
        ),
    )

    score = subparsers.add_parser(
        "score",
        help="Score a candidate image sequence against a prepared reference sequence",
    )
    score.add_argument("--candidate", required=True, help="Candidate image file or frame folder")
    score.add_argument("--reference", required=True, help="Reference image file or frame folder")
    score.add_argument("--output", required=True, help="Output path for the score report JSON")
    score.add_argument("--width", type=int, default=1920, help="Scoring width")
    score.add_argument("--height", type=int, default=1080, help="Scoring height")
    score.add_argument("--frame-count", type=int, default=None, help="Optional maximum number of frame pairs to score")
    score.add_argument("--ffmpeg", required=False, help="Optional path to ffmpeg")

    return parser


def _execute_job_command(
    repo_root: Path,
    harness_root: Path,
    config_dir: Path,
    job_path: Path,
    command_name: str,
    renderer: str | None = None,
    ffmpeg_path: str | None = None,
) -> dict:
    job = load_render_job(job_path)
    allowed_effects = load_allowed_effects(config_dir)
    validation = validate_job(job, repo_root, allowed_effects)

    if command_name == "validate":
        _print_validation(validation)
        return {
            "exit_code": 0 if validation.is_valid else 1,
            "validation_valid": validation.is_valid,
            "job_path": str(job_path),
            "planning_retrieval_summary": _summarize_retrieval_fields(job.planning),
        }

    if not validation.is_valid:
        _print_validation(validation)
        return {
            "exit_code": 1,
            "validation_valid": False,
            "job_path": str(job_path),
            "planning_retrieval_summary": _summarize_retrieval_fields(job.planning),
        }

    workspace = create_job_workspace(harness_root, job)
    write_json(workspace.inputs_dir / "job.normalized.json", job.to_dict())
    write_json(workspace.inputs_dir / "allowed_effects.json", allowed_effects)
    write_json(workspace.inputs_dir / "eval_thresholds.json", load_eval_thresholds(config_dir))

    if command_name == "prepare":
        return {
            "exit_code": 0,
            "validation_valid": True,
            "job_path": str(job_path),
            "workspace": str(workspace.root),
            "planning_retrieval_summary": _summarize_retrieval_fields(job.planning),
        }

    invocation = prepare_render_invocation(repo_root, workspace, job, renderer)
    similarity_report: dict | None = None
    similarity_report_file: Path | None = None
    demo_video_result: dict[str, object] | None = None
    demo_video_file: Path | None = None
    if command_name == "run" and job.inputs.reference_transition and invocation.produced_frame_count > 0:
        similarity_report_file = workspace.reports_dir / "similarity_score.json"
        reference_path = _resolve_path_argument(job.inputs.reference_transition, repo_root)
        try:
            similarity_report = _build_similarity_report(
                repo_root=repo_root,
                candidate=workspace.artifacts_dir,
                reference=reference_path,
                width=job.render.width,
                height=job.render.height,
                frame_count=job.render.frame_count,
                output=similarity_report_file,
                ffmpeg_path=ffmpeg_path,
            )
        except Exception as exc:
            similarity_report = {
                "report_type": "similarity_score",
                "report_version": 1,
                "candidate": _format_path_for_output(workspace.artifacts_dir, repo_root),
                "reference": _format_path_for_output(reference_path, repo_root),
                "status": "failed",
                "error": str(exc),
            }
            write_json(similarity_report_file, similarity_report)

    if command_name == "run" and invocation.produced_frame_count > 0:
        demo_video_file = workspace.artifacts_dir / "rendered.mp4"
        demo_video_result = encode_render_demo_video(
            artifacts_dir=workspace.artifacts_dir,
            output_file=demo_video_file,
            fps=job.render.fps,
            ffmpeg_path=ffmpeg_path,
        )
        invocation.demo_video_file = str(demo_video_file)
        invocation.demo_video_status = str(demo_video_result.get("status"))
        invocation.demo_video_message = str(demo_video_result.get("message"))
        exit_code = demo_video_result.get("exit_code")
        invocation.demo_video_exit_code = int(exit_code) if isinstance(exit_code, int) else None

    evaluation = _build_run_evaluation_summary(
        invocation,
        similarity_report,
        similarity_report_file,
        job.planning,
    )
    report = HarnessReport(
        status=_resolve_run_report_status(invocation.status, similarity_report),
        summary=_resolve_run_report_summary(invocation.message, similarity_report),
        data={
            "workspace": str(workspace.root),
            "planning": job.planning,
            "renderer_executable": invocation.renderer_executable,
            "request_file": str(invocation.request_file),
            "renderer_result_file": str(invocation.result_file),
            "expected_output_dir": str(invocation.expected_output_dir),
            "exit_code": invocation.exit_code,
            "stdout": invocation.stdout,
            "stderr": invocation.stderr,
            "produced_frame_count": invocation.produced_frame_count,
            "expected_frame_count": invocation.expected_frame_count,
            "output_check_message": invocation.output_check_message,
            "renderer_result": invocation.renderer_result,
            "demo_video_file": invocation.demo_video_file,
            "demo_video_status": invocation.demo_video_status,
            "demo_video_message": invocation.demo_video_message,
            "demo_video_exit_code": invocation.demo_video_exit_code,
            "demo_video_result": demo_video_result,
            "similarity_report_file": str(similarity_report_file) if similarity_report_file is not None else None,
            "similarity_report": similarity_report,
            "evaluation": evaluation,
        },
    )
    report_path = workspace.reports_dir / "run_report.json"
    report.write(report_path)

    return {
        "exit_code": 0 if _resolve_run_report_status(invocation.status, similarity_report) in {"succeeded", "blocked"} else 1,
        "validation_valid": True,
        "job_path": str(job_path),
        "workspace": str(workspace.root),
        "inputs_dir": str(workspace.inputs_dir),
        "render_dir": str(workspace.render_dir),
        "reports_dir": str(workspace.reports_dir),
        "artifacts_dir": str(workspace.artifacts_dir),
        "report": str(report_path),
        "status": _resolve_run_report_status(invocation.status, similarity_report),
        "summary": _resolve_run_report_summary(invocation.message, similarity_report),
        "demo_video_file": str(demo_video_file) if demo_video_file is not None else None,
        "demo_video_result": demo_video_result,
        "planning_retrieval_summary": _summarize_retrieval_from_evaluation(evaluation),
        "evaluation": evaluation,
    }


def _handle_prepare_video(args, repo_root: Path) -> int:
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (repo_root / output_dir).resolve()

    try:
        if args.solid_color:
            result = prepare_solid_color_frames(
                output_dir=output_dir,
                color=args.solid_color,
                width=args.width,
                height=args.height,
                frame_count=args.frame_count or 30,
                fps=args.fps,
            )
        else:
            source_video = Path(args.source_video)
            if not source_video.is_absolute():
                source_video = (repo_root / source_video).resolve()
            result = extract_video_frames(
                source_video=source_video,
                output_dir=output_dir,
                fps=args.fps,
                width=args.width,
                height=args.height,
                frame_count=args.frame_count,
                ffmpeg_path=args.ffmpeg,
            )
    except Exception as exc:
        print(f"prepare-video failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "mode": result.mode,
                "output_dir": str(result.output_dir),
                "frame_count": result.frame_count,
                "manifest_file": str(result.manifest_file),
                "message": result.message,
            },
            indent=2,
        )
    )
    return 0


def _handle_prepare_pair(args, repo_root: Path) -> int:
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = (repo_root / output_root).resolve()

    source_a_dir = output_root / "source_a"
    source_b_dir = output_root / "source_b"

    try:
        result_a = prepare_solid_color_frames(
            output_dir=source_a_dir,
            color=args.color_a,
            width=args.width,
            height=args.height,
            frame_count=args.frame_count,
            fps=args.fps,
        )
        result_b = prepare_solid_color_frames(
            output_dir=source_b_dir,
            color=args.color_b,
            width=args.width,
            height=args.height,
            frame_count=args.frame_count,
            fps=args.fps,
        )
    except Exception as exc:
        print(f"prepare-pair failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "mode": "solid_color_pair",
                "output_root": str(output_root),
                "source_a": {
                    "color": args.color_a,
                    "output_dir": str(result_a.output_dir),
                    "frame_count": result_a.frame_count,
                    "manifest_file": str(result_a.manifest_file),
                },
                "source_b": {
                    "color": args.color_b,
                    "output_dir": str(result_b.output_dir),
                    "frame_count": result_b.frame_count,
                    "manifest_file": str(result_b.manifest_file),
                },
                "message": f"generated paired fixtures at {output_root}",
            },
            indent=2,
        )
    )
    return 0


def _handle_score(args, repo_root: Path) -> int:
    candidate = _resolve_path_argument(args.candidate, repo_root)
    reference = _resolve_path_argument(args.reference, repo_root)
    output = _resolve_path_argument(args.output, repo_root)

    try:
        similarity_report = _build_similarity_report(
            repo_root=repo_root,
            candidate=candidate,
            reference=reference,
            width=args.width,
            height=args.height,
            frame_count=args.frame_count,
            output=output,
            ffmpeg_path=args.ffmpeg,
        )
    except Exception as exc:
        print(f"score failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "score_output": str(output),
                "frame_count": similarity_report["score"]["frame_count"],
                "mse": similarity_report["score"]["mse"],
                "mae": similarity_report["score"]["mae"],
                "psnr_db": similarity_report["score"]["psnr_db"],
            },
            indent=2,
        )
    )
    return 0


def _handle_index_effects(args, repo_root: Path) -> int:
    output = (
        _resolve_path_argument(args.output, repo_root)
        if args.output
        else (repo_root / "harness" / "configs" / "effect_catalog.json").resolve()
    )
    source_manifest = (
        _resolve_path_argument(args.source_manifest, repo_root)
        if args.source_manifest
        else None
    )

    try:
        catalog = build_effect_catalog(repo_root, source_manifest_path=source_manifest)
        write_json(output, catalog)
    except Exception as exc:
        print(f"index-effects failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "output": str(output),
                "catalog_type": catalog.get("catalog_type"),
                "catalog_version": catalog.get("catalog_version"),
                "source_manifest": catalog.get("source_manifest"),
                "source_manifest_version": catalog.get("source_manifest_version"),
                "source_manifest_sha256": catalog.get("source_manifest_sha256"),
                "registration_count": catalog.get("registration_count"),
                "effect_count": len(catalog.get("effects", [])),
            },
            indent=2,
        )
    )
    return 0


def _handle_audit_effects(args, repo_root: Path) -> int:
    output = _resolve_path_argument(args.output, repo_root) if args.output else None
    source_manifest = (
        _resolve_path_argument(args.source_manifest, repo_root)
        if args.source_manifest
        else None
    )

    try:
        audit = build_effect_catalog_audit(repo_root, source_manifest_path=source_manifest)
    except Exception as exc:
        print(f"audit-effects failed: {exc}")
        return 1

    if output is not None:
        write_json(output, audit)
    print(json.dumps(audit, indent=2))
    return 0 if audit.get("status") == "ok" else 1


def _handle_prepare_reference_transition(args, repo_root: Path) -> int:
    source_video = _resolve_path_argument(args.source_video, repo_root)
    output_dir = _resolve_path_argument(args.output_dir, repo_root)

    try:
        result = prepare_reference_transition(
            source_video=source_video,
            output_dir=output_dir,
            fps=args.fps,
            width=args.width,
            height=args.height,
            target_frame_count=args.target_frame_count,
            ffmpeg_path=args.ffmpeg,
            analysis_width=args.analysis_width,
            analysis_height=args.analysis_height,
        )
    except Exception as exc:
        print(f"prepare-reference-transition failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "frame_count": result.frame_count,
                "manifest_file": str(result.manifest_file),
                "detected_start_frame": result.detected_start_frame,
                "detected_end_frame": result.detected_end_frame,
                "detected_frame_count": result.detected_frame_count,
                "message": result.message,
            },
            indent=2,
        )
    )
    return 0


def _build_similarity_report(
    repo_root: Path,
    candidate: Path,
    reference: Path,
    width: int,
    height: int,
    frame_count: int | None,
    output: Path,
    ffmpeg_path: str | None = None,
) -> dict[str, object]:
    reference_manifest = load_reference_transition_manifest(reference)
    eval_thresholds = load_eval_thresholds(repo_root / "harness" / "configs")
    expected_frame_count = frame_count
    if reference_manifest is not None:
        manifest_frame_count = reference_manifest.get("frame_count")
        if not isinstance(manifest_frame_count, int) or manifest_frame_count < 2:
            raise ValueError("reference transition manifest frame_count must be an integer >= 2")
        if expected_frame_count is None:
            expected_frame_count = manifest_frame_count
        elif expected_frame_count != manifest_frame_count:
            raise ValueError(
                f"prepared reference frame_count mismatch: render expects {expected_frame_count}, "
                f"manifest provides {manifest_frame_count}"
            )

    score = score_frame_sequences(
        candidate=candidate,
        reference=reference,
        width=width,
        height=height,
        frame_count=expected_frame_count,
        ffmpeg_path=ffmpeg_path,
        require_exact_frame_count=reference_manifest is not None,
    )
    threshold_report = _build_similarity_threshold_report(score.to_dict(), eval_thresholds)
    similarity_report = {
        "report_type": "similarity_score",
        "report_version": 1,
        "candidate": _format_path_for_output(candidate, repo_root),
        "reference": _format_path_for_output(reference, repo_root),
        "status": "succeeded",
        "thresholds": eval_thresholds,
        "alignment": _build_similarity_alignment(
            repo_root=repo_root,
            reference=reference,
            expected_frame_count=expected_frame_count,
            score=score,
            reference_manifest=reference_manifest,
        ),
        "score": score.to_dict(),
        "threshold_evaluation": threshold_report,
    }
    if threshold_report["status"] == "failed":
        similarity_report["status"] = "failed"
        similarity_report["error"] = threshold_report["message"]
    write_json(output, similarity_report)
    return similarity_report


def _build_similarity_threshold_report(score: dict[str, object], eval_thresholds: dict[str, object]) -> dict[str, object]:
    metrics = eval_thresholds.get("metrics") if isinstance(eval_thresholds, dict) else None
    if not isinstance(metrics, dict):
        return {
            "status": "blocked",
            "message": "eval_thresholds.json is missing metrics",
            "checks": {},
        }

    checks: dict[str, dict[str, object]] = {}
    failed = False
    for metric_name, metric_value in (
        ("mse", score.get("mse")),
        ("mae", score.get("mae")),
        ("psnr_db", score.get("psnr_db")),
        ("ssim", score.get("ssim")),
    ):
        threshold = metrics.get(metric_name)
        if not isinstance(threshold, dict):
            continue
        warn = threshold.get("warn")
        fail = threshold.get("fail")
        if metric_value is None:
            state = "pass" if metric_name == "psnr_db" else "missing"
        elif metric_name == "psnr_db":
            state = "pass"
            if isinstance(fail, (int, float)) and metric_value < fail:
                state = "fail"
            elif isinstance(warn, (int, float)) and metric_value < warn:
                state = "warn"
        elif metric_name == "ssim":
            state = "pass"
            if isinstance(fail, (int, float)) and metric_value < fail:
                state = "fail"
            elif isinstance(warn, (int, float)) and metric_value < warn:
                state = "warn"
        else:
            state = "pass"
            if isinstance(fail, (int, float)) and metric_value > fail:
                state = "fail"
            elif isinstance(warn, (int, float)) and metric_value > warn:
                state = "warn"

        if state == "fail":
            failed = True
        checks[metric_name] = {
            "value": metric_value,
            "warn": warn,
            "fail": fail,
            "status": state,
        }

    if failed:
        return {
            "status": "failed",
            "message": "one or more similarity metrics exceeded the fail threshold",
            "checks": checks,
        }

    return {
        "status": "passed",
        "message": "all checked similarity metrics were within threshold",
        "checks": checks,
    }


def _build_similarity_alignment(
    repo_root: Path,
    reference: Path,
    expected_frame_count: int | None,
    score,
    reference_manifest: dict | None,
) -> dict[str, object]:
    alignment = {
        "mode": "prepared_reference_manifest" if reference_manifest is not None else "frame_sequence_order",
        "strict_frame_count": reference_manifest is not None,
        "expected_frame_count": expected_frame_count,
        "candidate_frame_count": score.candidate_frame_count,
        "reference_frame_count": score.reference_frame_count,
    }
    if reference_manifest is None:
        return alignment

    manifest_path = reference / "reference_transition_manifest.json" if reference.is_dir() else reference
    analysis = reference_manifest.get("analysis")
    alignment["reference_manifest"] = {
        "manifest_path": _format_path_for_output(manifest_path, repo_root),
        "source_video": reference_manifest.get("source_video"),
        "frame_count": reference_manifest.get("frame_count"),
        "requested_frame_count": reference_manifest.get("requested_frame_count"),
        "analysis": {
            "normalized_clip_frame_count": analysis.get("normalized_clip_frame_count") if isinstance(analysis, dict) else None,
            "detected_start_frame": analysis.get("detected_start_frame") if isinstance(analysis, dict) else None,
            "detected_end_frame": analysis.get("detected_end_frame") if isinstance(analysis, dict) else None,
            "detected_frame_count": analysis.get("detected_frame_count") if isinstance(analysis, dict) else None,
        },
        "frame_progress_mapping": reference_manifest.get("frame_progress_mapping"),
    }
    return alignment


def _build_run_evaluation_summary(
    invocation,
    similarity_report: dict | None,
    similarity_report_file: Path | None,
    planning: dict | None,
) -> dict[str, object]:
    score_status = None
    score_alignment_mode = None
    score_frame_count = None
    score_error = None
    score_threshold_status = None
    score_threshold_checks = None
    retrieval_status = None
    retrieval_effect_id = None
    retrieval_mode = None
    retrieval_fallback_used = None
    retrieval_fallback_mode = None
    retrieval_fallback_preset = None
    retrieval_fallback_reason = None
    retrieval_match_kind = None
    retrieval_matched_style_hint = None
    retrieval_candidate_count = None
    if isinstance(planning, dict):
        retrieval = planning.get("retrieval")
        if isinstance(retrieval, dict):
            retrieval_status = retrieval.get("status")
            retrieval_effect_id = retrieval.get("effect_id")
            retrieval_mode = retrieval.get("mode")
            retrieval_fallback_used = retrieval.get("fallback_used")
            retrieval_fallback_mode = retrieval.get("fallback_mode")
            retrieval_fallback_preset = retrieval.get("fallback_preset")
            retrieval_fallback_reason = retrieval.get("fallback_reason")
            retrieval_match_kind = retrieval.get("match_kind")
            retrieval_matched_style_hint = retrieval.get("matched_style_hint")
            retrieval_candidate_count = retrieval.get("candidate_count")
    if similarity_report is not None:
        score_status = similarity_report.get("status")
        score_alignment = similarity_report.get("alignment")
        if isinstance(score_alignment, dict):
            score_alignment_mode = score_alignment.get("mode")
        score_payload = similarity_report.get("score")
        if isinstance(score_payload, dict):
            score_frame_count = score_payload.get("frame_count")
        score_error = similarity_report.get("error")
        threshold_evaluation = similarity_report.get("threshold_evaluation")
        if isinstance(threshold_evaluation, dict):
            score_threshold_status = threshold_evaluation.get("status")
            score_threshold_checks = threshold_evaluation.get("checks")

    return {
        "render": {
            "status": invocation.status,
            "exit_code": invocation.exit_code,
            "produced_frame_count": invocation.produced_frame_count,
            "expected_frame_count": invocation.expected_frame_count,
            "message": invocation.message,
        },
        "score": {
            "status": score_status,
            "alignment_mode": score_alignment_mode,
            "frame_count": score_frame_count,
            "report_file": str(similarity_report_file) if similarity_report_file is not None else None,
            "error": score_error,
            "ssim": similarity_report.get("score", {}).get("ssim") if isinstance(similarity_report, dict) else None,
            "threshold_status": score_threshold_status,
            "threshold_checks": score_threshold_checks,
        },
        "planning": {
            "retrieval_status": retrieval_status,
            "retrieval_effect_id": retrieval_effect_id,
            "retrieval_mode": retrieval_mode,
            "retrieval_fallback_used": retrieval_fallback_used,
            "retrieval_fallback_mode": retrieval_fallback_mode,
            "retrieval_fallback_preset": retrieval_fallback_preset,
            "retrieval_fallback_reason": retrieval_fallback_reason,
            "retrieval_match_kind": retrieval_match_kind,
            "retrieval_matched_style_hint": retrieval_matched_style_hint,
            "retrieval_candidate_count": retrieval_candidate_count,
        },
        "overall_status": _resolve_run_overall_status(invocation.status, score_status),
    }


def _resolve_run_overall_status(render_status: str, score_status: str | None) -> str:
    if render_status not in {"succeeded", "blocked"}:
        return "render_failed"
    if score_status == "failed":
        return "score_failed"
    if score_status == "succeeded":
        return "succeeded_with_score"
    return render_status


def _resolve_run_report_status(render_status: str, similarity_report: dict | None) -> str:
    if render_status not in {"succeeded", "blocked"}:
        return "failed"
    if similarity_report is not None and similarity_report.get("status") == "failed":
        return "failed"
    return render_status


def _resolve_run_report_summary(render_summary: str, similarity_report: dict | None) -> str:
    if similarity_report is None:
        return render_summary
    if similarity_report.get("status") != "failed":
        return render_summary

    error = similarity_report.get("error")
    if error:
        return f"{render_summary}; scoring failed: {error}"
    return f"{render_summary}; scoring failed"


def _handle_analyze_transition(args, repo_root: Path) -> int:
    source_a = _resolve_path_argument(args.source_a, repo_root)
    source_b = _resolve_path_argument(args.source_b, repo_root)
    hint_output = _resolve_path_argument(args.hint_output, repo_root)
    analysis_output = _resolve_analysis_output(args.analysis_output, hint_output)
    comparison_output = (
        _resolve_path_argument(args.comparison_output, repo_root) if args.comparison_output else None
    )
    analysis_provider_config = load_analysis_provider_config(repo_root / "harness" / "configs")
    metadata_inputs: dict | None = None

    if args.clip_metadata_file:
        metadata_path = _resolve_path_argument(args.clip_metadata_file, repo_root)
        try:
            metadata_inputs = derive_analyzer_inputs_from_metadata(load_clip_metadata(metadata_path))
        except Exception as exc:
            print(f"analyze-transition failed: could not load clip metadata file: {exc}")
            return 1

    reference_transition = (
        _resolve_path_argument(args.reference_transition, repo_root)
        if args.reference_transition
        else _resolve_path_argument(str(metadata_inputs["reference_transition"]), repo_root)
        if metadata_inputs and metadata_inputs.get("reference_transition")
        else None
    )

    try:
        analyzer_inputs = {
            "input_kind": (metadata_inputs.get("input_kind") if metadata_inputs else None) or args.input_kind,
            "style_hint": args.style_hint or (metadata_inputs.get("style_hint") if metadata_inputs else None),
            "intent": args.intent,
            "prefer_generated": args.prefer_generated or bool(metadata_inputs and metadata_inputs.get("prefer_generated")),
            "analysis_provider_kind": args.analysis_provider_kind,
            "analysis_provider_name": args.analysis_provider_name or (metadata_inputs.get("analysis_provider_name") if metadata_inputs else None),
            "analysis_provider_mode": args.analysis_provider_mode,
            "analysis_provider_configuration": analysis_provider_config,
            "reference_transition": _format_path_for_output(reference_transition, repo_root),
            "job_name": args.job_name or (metadata_inputs.get("job_name") if metadata_inputs else None),
            "clip_metadata_file": args.clip_metadata_file,
        }
        hint = analyze_transition(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            input_kind=analyzer_inputs["input_kind"],
            style_hint=analyzer_inputs["style_hint"],
            intent=analyzer_inputs["intent"],
            prefer_generated=analyzer_inputs["prefer_generated"],
            reference_transition=reference_transition,
            job_name=analyzer_inputs["job_name"],
            provider_request={
                "kind": analyzer_inputs["analysis_provider_kind"],
                "name": analyzer_inputs["analysis_provider_name"],
                "mode": analyzer_inputs["analysis_provider_mode"],
            },
            provider_configuration=analysis_provider_config,
        )
        if metadata_inputs and metadata_inputs.get("style_reason"):
            hint["analysis"]["style_reason"] = metadata_inputs["style_reason"]
            hint["notes"] = (
                f"Analyzer selected '{hint['style_hint']}' because {metadata_inputs['style_reason']}."
            )
        if metadata_inputs and metadata_inputs.get("notes"):
            existing_notes = hint.get("notes") or ""
            hint["notes"] = f"{existing_notes} Metadata notes: {metadata_inputs['notes']}".strip()
            hint["analysis"]["clip_metadata_file"] = args.clip_metadata_file
        write_json(hint_output, hint)
        analysis_artifact = build_transition_analysis_artifact(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            analyzer_inputs=analyzer_inputs,
            hint=hint,
        )
        write_json(analysis_output, analysis_artifact)

        if comparison_output is not None:
            embedded_plan = extract_plan_from_analysis(analysis_artifact)
            resolved_facts = extract_resolved_facts_from_analysis(analysis_artifact)
            if not embedded_plan or not resolved_facts:
                raise ValueError("analysis artifact is missing planning or resolved facts for comparison output")

            recomputed_plan = build_recommended_plan(
                repo_root=repo_root,
                source_a=source_a,
                source_b=source_b,
                hint_data={
                    "style_hint": resolved_facts.get("style_hint"),
                    "input_kind": resolved_facts.get("input_kind"),
                    "job_name": resolved_facts.get("job_name"),
                    "reference_transition": analysis_artifact.get("sources", {}).get("reference_transition"),
                },
            )
            comparison_report = _build_plan_comparison_report(
                analysis_file=_format_path_for_output(analysis_output, repo_root),
                job_output=None,
                plan_source="analyze_transition_embedded_and_recomputed",
                selected_plan=_summarize_plan_fields(embedded_plan),
                selected_plan_retrieval_summary=_summarize_retrieval_fields(embedded_plan),
                embedded_plan=embedded_plan,
                embedded_plan_summary=_summarize_plan_fields(embedded_plan),
                recomputed_plan=recomputed_plan,
                recomputed_plan_summary=_summarize_plan_fields(recomputed_plan),
                recompute_matches_embedded=(
                    _summarize_plan_fields(embedded_plan) == _summarize_plan_fields(recomputed_plan)
                ),
                validation_valid=True,
                issues=[],
            )
            write_json(comparison_output, comparison_report)
    except Exception as exc:
        print(f"analyze-transition failed: {exc}")
        return 1

    analysis_provider_summary = _build_analysis_provider_artifact_summary(analysis_artifact)
    print(
        json.dumps(
            {
                "hint_output": str(hint_output),
                "analysis_output": str(analysis_output),
                "analysis_artifact": analysis_artifact,
                "analysis_provider_request": analysis_provider_summary["request"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_requested": analysis_provider_summary["requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_selected": analysis_provider_summary["selected"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_delegation": analysis_provider_summary["delegation"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution": analysis_provider_summary["resolution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_requested": analysis_provider_summary["resolution_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_resolved": analysis_provider_summary["resolution_resolved"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_configuration": analysis_provider_summary["resolution_configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration": analysis_provider_summary["configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_status": analysis_provider_summary["resolution_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_reason": analysis_provider_summary["resolution_reason"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_loaded": analysis_provider_summary["configuration_loaded"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_path": analysis_provider_summary["configuration_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_type": analysis_provider_summary["configuration_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_version": analysis_provider_summary["configuration_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_source": analysis_provider_summary["configuration_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_enabled": analysis_provider_summary["configuration_model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider": analysis_provider_summary["configuration_default_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider": analysis_provider_summary["configuration_model_backed_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_kind": analysis_provider_summary["configuration_default_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_name": analysis_provider_summary["configuration_default_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_mode": analysis_provider_summary["configuration_default_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_kind": analysis_provider_summary["configuration_model_backed_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_name": analysis_provider_summary["configuration_model_backed_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_mode": analysis_provider_summary["configuration_model_backed_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_enabled": analysis_provider_summary["configuration_model_backed_provider_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_source": analysis_provider_summary["configuration_model_backed_provider_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_adapter": analysis_provider_summary["adapter"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_runtime": analysis_provider_summary["runtime"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution": analysis_provider_summary["execution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_request_contract": analysis_provider_summary["model_execution_request_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_result_contract": analysis_provider_summary["model_execution_result_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_type": analysis_provider_summary["execution_contract_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_version": analysis_provider_summary["execution_contract_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_entry_point": analysis_provider_summary["execution_entry_point"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_kind": analysis_provider_summary["selected_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_name": analysis_provider_summary["selected_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_mode": analysis_provider_summary["selected_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_contract": analysis_provider_summary["model_execution_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_ready": analysis_provider_summary["model_execution_ready"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_status": analysis_provider_summary["runtime"]["execution"]["implementation_status"] if isinstance(analysis_provider_summary, dict) and isinstance(analysis_provider_summary.get("runtime"), dict) and isinstance(analysis_provider_summary["runtime"].get("execution"), dict) else None,
                "analysis_model_execution_mode": analysis_provider_summary["runtime"]["execution"]["execution_mode"] if isinstance(analysis_provider_summary, dict) and isinstance(analysis_provider_summary.get("runtime"), dict) and isinstance(analysis_provider_summary["runtime"].get("execution"), dict) else None,
                "analysis_model_delegation_path": analysis_provider_summary["delegation_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_requested": analysis_provider_summary["model_backed_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_enabled": analysis_provider_summary["model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_source": analysis_provider_summary["analysis_source"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_summary": analysis_provider_summary["transition_summary"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video_analysis": analysis_provider_summary["transition_video_analysis"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video": analysis_provider_summary["transition_video"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_window": analysis_provider_summary["transition_window"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_progression": analysis_provider_summary["transition_progression"] if isinstance(analysis_provider_summary, dict) else None,
                "comparison_output": str(comparison_output) if comparison_output is not None else None,
                "style_hint": hint.get("style_hint"),
                "input_kind": hint.get("input_kind"),
                "job_name": hint.get("job_name"),
                "notes": hint.get("notes"),
            },
            indent=2,
        )
    )
    return 0


def _handle_analyze_sample_video(args, repo_root: Path) -> int:
    output_root = _resolve_path_argument(args.output_root, repo_root)
    analysis_root = output_root / f"sample_video_analysis_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    analysis_root.mkdir(parents=True, exist_ok=False)

    reference_output = analysis_root / "reference_transition"
    hint_output = analysis_root / "transition_hint.json"
    analysis_output = _resolve_path_argument(args.analysis_output, repo_root) if args.analysis_output else analysis_root / "transition_analysis.json"
    comparison_output = _resolve_path_argument(args.comparison_output, repo_root) if args.comparison_output else None

    source_a = _resolve_path_argument(args.source_a, repo_root)
    source_b = _resolve_path_argument(args.source_b, repo_root)
    transition_video = _resolve_path_argument(args.transition_video, repo_root)
    try:
        reference_result = prepare_reference_transition(
            source_video=transition_video,
            output_dir=reference_output,
            fps=args.fps,
            width=args.width,
            height=args.height,
            target_frame_count=args.target_frame_count,
            ffmpeg_path=args.ffmpeg,
            analysis_width=args.analysis_width,
            analysis_height=args.analysis_height,
        )
        hint = analyze_transition_video(
            repo_root=repo_root,
            transition_video=transition_video,
            input_kind=args.input_kind,
            style_hint=args.style_hint,
            intent=args.intent,
            prefer_generated=args.prefer_generated,
            reference_transition=reference_output,
            job_name=args.job_name,
            transition_window=_summarize_reference_transition_window(reference_result),
        )
        write_json(hint_output, hint)
        analyzer_inputs = {
            "input_kind": args.input_kind,
            "style_hint": args.style_hint,
            "intent": args.intent,
            "prefer_generated": args.prefer_generated,
            "analysis_mode": "deterministic_rules",
            "analysis_source": "transition_video",
            "analysis_engine": ANALYSIS_ENGINE,
            "transition_video": _format_path_for_output(transition_video, repo_root),
            "transition_window": _summarize_reference_transition_window(reference_result),
            "reference_transition": _format_path_for_output(reference_output, repo_root),
            "job_name": args.job_name,
            "sample_video": True,
        }
        analysis_artifact = build_transition_analysis_artifact(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            analyzer_inputs=analyzer_inputs,
            hint=hint,
        )
        write_json(analysis_output, analysis_artifact)

        if comparison_output is not None:
            embedded_plan = extract_plan_from_analysis(analysis_artifact)
            resolved_facts = extract_resolved_facts_from_analysis(analysis_artifact)
            if not embedded_plan or not resolved_facts:
                raise ValueError("analysis artifact is missing planning or resolved facts for comparison output")

            recomputed_plan = build_recommended_plan(
                repo_root=repo_root,
                source_a=source_a,
                source_b=source_b,
                hint_data={
                    "style_hint": resolved_facts.get("style_hint"),
                    "input_kind": resolved_facts.get("input_kind"),
                    "job_name": resolved_facts.get("job_name"),
                    "reference_transition": analysis_artifact.get("sources", {}).get("reference_transition"),
                },
            )
            comparison_report = _build_plan_comparison_report(
                analysis_file=_format_path_for_output(analysis_output, repo_root),
                job_output=None,
                plan_source="analyze_sample_video_embedded_and_recomputed",
                selected_plan=_summarize_plan_fields(embedded_plan),
                selected_plan_retrieval_summary=_summarize_retrieval_fields(embedded_plan),
                embedded_plan=embedded_plan,
                embedded_plan_summary=_summarize_plan_fields(embedded_plan),
                recomputed_plan=recomputed_plan,
                recomputed_plan_summary=_summarize_plan_fields(recomputed_plan),
                recompute_matches_embedded=(
                    _summarize_plan_fields(embedded_plan) == _summarize_plan_fields(recomputed_plan)
                ),
                validation_valid=True,
                issues=[],
            )
            write_json(comparison_output, comparison_report)
    except Exception as exc:
        print(f"analyze-sample-video failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "hint_output": str(hint_output),
                "analysis_output": str(analysis_output),
                "analysis_artifact": analysis_artifact,
                "comparison_output": str(comparison_output) if comparison_output is not None else None,
                "style_hint": hint.get("style_hint"),
                "input_kind": hint.get("input_kind"),
                "job_name": hint.get("job_name"),
                "notes": hint.get("notes"),
            },
            indent=2,
        )
    )
    return 0


def _handle_flow(args, repo_root: Path, harness_root: Path, config_dir: Path, default_renderer: str | None) -> int:
    output_root = _resolve_path_argument(args.output_root, repo_root)
    flow_root = output_root / f"transition_flow_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    flow_root.mkdir(parents=True, exist_ok=False)

    reference_output = flow_root / "reference_transition"
    hint_output = flow_root / "transition_hint.json"
    analysis_output = flow_root / "transition_analysis.json"
    job_output = flow_root / "planned.render_job.json"
    report_output = flow_root / "flow_report.json"
    effect_spec_output_raw = _resolve_path_argument(args.effect_spec_output, repo_root) if args.effect_spec_output else None
    effect_spec_output = effect_spec_output_raw

    source_a = _resolve_path_argument(args.source_a, repo_root)
    source_b = _resolve_path_argument(args.source_b, repo_root)
    transition_video = _resolve_path_argument(args.transition_video, repo_root)
    analysis_provider_config = load_analysis_provider_config(config_dir)
    renderer = _resolve_renderer_argument(args.renderer, default_renderer)

    reference_result = None
    hint = None
    analysis_artifact = None
    planning = None
    job = None
    effect_spec_payload = None
    validation = None
    run_result: dict | None = None
    flow_error: str | None = None
    frame_count_source = None

    try:
        reference_result = prepare_reference_transition(
            source_video=transition_video,
            output_dir=reference_output,
            fps=args.fps,
            width=args.width,
            height=args.height,
            target_frame_count=args.target_frame_count,
            ffmpeg_path=args.ffmpeg,
            analysis_width=args.analysis_width,
            analysis_height=args.analysis_height,
        )
        hint = analyze_transition_video(
            repo_root=repo_root,
            transition_video=transition_video,
            input_kind=args.input_kind,
            style_hint=args.style_hint,
            intent=args.intent,
            prefer_generated=args.prefer_generated,
            reference_transition=reference_output,
            job_name=args.job_name,
            transition_window=_summarize_reference_transition_window(reference_result),
            provider_request={
                "kind": args.analysis_provider_kind,
                "name": args.analysis_provider_name,
                "mode": args.analysis_provider_mode,
            },
            provider_configuration=analysis_provider_config,
        )
        write_json(hint_output, hint)
        analyzer_inputs = {
            "input_kind": args.input_kind,
            "style_hint": args.style_hint,
            "intent": args.intent,
            "prefer_generated": args.prefer_generated,
            "analysis_provider_kind": args.analysis_provider_kind,
            "analysis_provider_name": args.analysis_provider_name,
            "analysis_provider_mode": args.analysis_provider_mode,
            "analysis_provider_configuration": analysis_provider_config,
            "analysis_mode": "deterministic_rules",
            "analysis_source": "transition_video",
            "analysis_engine": ANALYSIS_ENGINE,
            "transition_video": _format_path_for_output(transition_video, repo_root),
            "transition_window": _summarize_reference_transition_window(reference_result),
            "reference_transition": _format_path_for_output(reference_output, repo_root),
            "job_name": args.job_name,
            "flow": True,
        }
        analysis_artifact = build_transition_analysis_artifact(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            analyzer_inputs=analyzer_inputs,
            hint=hint,
        )
        transition_planning_hint = _build_transition_planning_hint(
            transition_video=transition_video,
            reference_output=reference_output,
            args=args,
            repo_root=repo_root,
        )
        transition_planning = None
        try:
            transition_planning = build_recommended_plan(
                repo_root=repo_root,
                source_a=source_a,
                source_b=source_b,
                hint_data=transition_planning_hint,
            )
        except Exception as exc:
            transition_planning = None

        if not isinstance(transition_planning, dict):
            transition_planning = analysis_artifact.get("planning_recommendation")
        if not isinstance(transition_planning, dict):
            raise ValueError("transition analysis artifact did not include a planning recommendation")
        analysis_artifact["planning_recommendation"] = {
            **transition_planning,
            "producer": "transition_video_analysis",
            "analysis_engine": ANALYSIS_ENGINE,
            "transition_planning_hint": transition_planning_hint,
        }
        write_json(analysis_output, analysis_artifact)
        planning = analysis_artifact.get("planning_recommendation")

        if effect_spec_output is None and str(planning.get("mode") or "").startswith("generated-"):
            effect_spec_output = flow_root / "planned.effect_spec.json"

        resolved_frame_count, frame_count_source = resolve_planned_frame_count(
            reference_transition=reference_output,
            explicit_frame_count=args.frame_count,
        )
        planned_job_name = args.job_name or (str(planning.get("job_name")) if planning.get("job_name") else None)
        job, effect_spec_payload = build_planned_job(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            mode=str(planning.get("mode")),
            width=args.width,
            height=args.height,
            fps=args.fps,
            frame_count=resolved_frame_count,
            output_format="png_sequence",
            job_name=planned_job_name,
            reference_transition=reference_output,
            effect_spec_output=effect_spec_output,
            planning=planning,
        )
        planning = getattr(job, "planning", planning)

        if effect_spec_output is not None and effect_spec_payload is not None:
            write_json(effect_spec_output, effect_spec_payload)

        write_json(job_output, job.to_dict())
        validation = validate_job(job, repo_root, load_allowed_effects(config_dir))

        if validation.is_valid:
            run_result = _execute_job_command(
                repo_root=repo_root,
                harness_root=harness_root,
                config_dir=config_dir,
                job_path=job_output,
                command_name="run",
                renderer=renderer,
                ffmpeg_path=args.ffmpeg,
            )
    except Exception as exc:
        flow_error = str(exc)

    report_data = _build_flow_report(
        repo_root=repo_root,
        flow_root=flow_root,
        transition_video=transition_video,
        source_a=source_a,
        source_b=source_b,
        reference_result=reference_result,
        hint_output=hint_output,
        analysis_output=analysis_output,
        analysis_artifact=analysis_artifact,
        planning=planning,
        job_output=job_output,
        job=job,
        validation=validation,
        run_result=run_result,
        effect_spec_output=effect_spec_output,
        flow_error=flow_error,
    )
    report_data.write(report_output)

    print(
        json.dumps(
            {
                "flow_report": str(report_output),
                "analysis_output": str(analysis_output),
                "workspace_paths": {
                    "flow_root": str(flow_root),
                    "reference_transition_dir": str(reference_result.output_dir) if reference_result is not None else None,
                    "reference_transition_manifest": str(reference_result.manifest_file) if reference_result is not None else None,
                    "hint_file": str(hint_output),
                    "analysis_file": str(analysis_output),
                    "job_file": str(job_output) if job_output is not None else None,
                    "effect_spec_file": str(effect_spec_output) if effect_spec_output is not None else None,
                    "similarity_report_file": run_result.get("evaluation", {}).get("score", {}).get("report_file")
                    if isinstance(run_result, dict)
                    else None,
                    "render_request_file": run_result.get("request_file") if isinstance(run_result, dict) else None,
                    "renderer_result_file": run_result.get("renderer_result_file") if isinstance(run_result, dict) else None,
                    "run_report": run_result.get("report") if isinstance(run_result, dict) else None,
                    "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
                },
                "analysis_artifact": analysis_artifact,
                "analysis_provider_request": report_data.data.get("analysis_provider_request"),
                "analysis_provider_requested": report_data.data.get("analysis_provider_requested"),
                "analysis_provider_selected": report_data.data.get("analysis_provider_selected"),
                "analysis_provider_delegation": report_data.data.get("analysis_provider_delegation"),
                "analysis_provider_resolution": report_data.data.get("analysis_provider_resolution"),
                "analysis_provider_resolution_requested": report_data.data.get("analysis_provider_resolution_requested"),
                "analysis_provider_resolution_resolved": report_data.data.get("analysis_provider_resolution_resolved"),
                "analysis_provider_resolution_configuration": report_data.data.get("analysis_provider_resolution_configuration"),
                "analysis_provider_resolution_status": report_data.data.get("analysis_provider_resolution_status"),
                "analysis_provider_resolution_reason": report_data.data.get("analysis_provider_resolution_reason"),
                "analysis_provider_configuration": report_data.data.get("analysis_provider_configuration"),
                "analysis_provider_configuration_type": report_data.data.get("analysis_provider_configuration_type"),
                "analysis_provider_configuration_loaded": report_data.data.get("analysis_provider_configuration_loaded"),
                "analysis_provider_configuration_path": report_data.data.get("analysis_provider_configuration_path"),
                "analysis_provider_configuration_version": report_data.data.get("analysis_provider_configuration_version"),
                "analysis_provider_configuration_source": report_data.data.get("analysis_provider_configuration_source"),
                "analysis_provider_configuration_model_backed_enabled": report_data.data.get("analysis_provider_configuration_model_backed_enabled"),
                "analysis_provider_configuration_default_provider": report_data.data.get("analysis_provider_configuration_default_provider"),
                "analysis_provider_configuration_model_backed_provider": report_data.data.get("analysis_provider_configuration_model_backed_provider"),
                "analysis_provider_configuration_default_provider_kind": report_data.data.get("analysis_provider_configuration_default_provider_kind"),
                "analysis_provider_configuration_default_provider_name": report_data.data.get("analysis_provider_configuration_default_provider_name"),
                "analysis_provider_configuration_default_provider_mode": report_data.data.get("analysis_provider_configuration_default_provider_mode"),
                "analysis_provider_configuration_model_backed_provider_kind": report_data.data.get("analysis_provider_configuration_model_backed_provider_kind"),
                "analysis_provider_configuration_model_backed_provider_name": report_data.data.get("analysis_provider_configuration_model_backed_provider_name"),
                "analysis_provider_configuration_model_backed_provider_mode": report_data.data.get("analysis_provider_configuration_model_backed_provider_mode"),
                "analysis_provider_configuration_model_backed_provider_enabled": report_data.data.get("analysis_provider_configuration_model_backed_provider_enabled"),
                "analysis_provider_configuration_model_backed_provider_source": report_data.data.get("analysis_provider_configuration_model_backed_provider_source"),
                "analysis_provider_adapter": report_data.data.get("analysis_provider_adapter"),
                "analysis_provider_runtime": report_data.data.get("analysis_provider_runtime"),
                "analysis_provider_execution": report_data.data.get("analysis_provider_execution"),
                "analysis_provider_execution_request_contract": report_data.data.get("analysis_provider_execution_request_contract"),
                "analysis_provider_execution_result_contract": report_data.data.get("analysis_provider_execution_result_contract"),
                "analysis_provider_execution_contract_type": report_data.data.get("analysis_provider_execution_contract_type"),
                "analysis_provider_execution_contract_version": report_data.data.get("analysis_provider_execution_contract_version"),
                "analysis_provider_execution_entry_point": report_data.data.get("analysis_provider_execution_entry_point"),
                "analysis_model_execution_contract": report_data.data.get("analysis_model_execution_contract"),
                "analysis_model_execution_ready": report_data.data.get("analysis_model_execution_ready"),
                "analysis_model_execution_status": report_data.data.get("analysis_model_execution_status"),
                "analysis_model_execution_mode": report_data.data.get("analysis_model_execution_mode"),
                "analysis_model_delegation_path": report_data.data.get("analysis_model_delegation_path"),
                "analysis_model_backed_requested": report_data.data.get("analysis_model_backed_requested"),
                "analysis_model_backed_enabled": report_data.data.get("analysis_model_backed_enabled"),
                "analysis_source": report_data.data.get("analysis_source"),
                "transition_video": report_data.data.get("transition_video"),
                "transition_window": report_data.data.get("transition_window"),
                "transition_progression": report_data.data.get("transition_progression"),
                "status": report_data.status,
                "summary": report_data.summary,
                "flow_root": str(flow_root),
            },
            indent=2,
        )
    )
    return 0 if report_data.status in {"succeeded", "blocked"} else 1


def _handle_sample_video(
    args,
    repo_root: Path,
    harness_root: Path,
    config_dir: Path,
    default_renderer: str | None,
) -> int:
    source_a = _resolve_path_argument(args.source_a, repo_root)
    source_b = _resolve_path_argument(args.source_b, repo_root)
    output_root = _resolve_path_argument(args.output_root, repo_root) if getattr(args, "output_root", None) else (harness_root / "work" / "tests")
    output_video = Path(args.output_video)
    if not output_video.is_absolute():
        output_video = (repo_root / output_video).resolve()
    sample_root = output_root / f"sample_video_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    sample_root.mkdir(parents=True, exist_ok=False)

    renderer = _resolve_renderer_argument(args.renderer, default_renderer)
    sample_job_output = sample_root / "sample.render_job.json"
    sample_report_output = sample_root / "sample_video_report.json"
    flow_error: str | None = None
    run_result: dict | None = None
    job = None
    planning: dict | None = None
    validation = None
    sample_hint: dict | None = None
    selected_fx_id = args.fx_id

    try:
        if selected_fx_id:
            job_name = args.job_name or f"sample_{_slugify_text(selected_fx_id)}"
            job = RenderJob(
                job_name=job_name,
                effect=EffectSpec(
                    fx_id=selected_fx_id,
                    category="single_pass",
                    effect_spec=None,
                    uniforms={"progress": 0.0},
                ),
                inputs=InputSpec(
                    source_a=_format_path_for_output(source_a, repo_root),
                    source_b=_format_path_for_output(source_b, repo_root),
                ),
                render=RenderSettings(
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    frame_count=args.frame_count,
                    output_format="png_sequence",
                ),
            )
            planning = {
                "auto": False,
                "mode": "explicit-fx-id",
                "preset": None,
                "job_name": job.job_name,
                "fx_id": selected_fx_id,
            }
        else:
            forced_mode = args.force_mode
            requested_style = args.style
            if forced_mode:
                job_name = args.job_name or PLANNER_MODES.get(forced_mode, {}).get("job_name") or forced_mode
                planning = {
                    "auto": False,
                    "mode": forced_mode,
                    "preset": None,
                    "job_name": job_name,
                    "forced": True,
                    "style": requested_style,
                }
                if requested_style is not None:
                    planning["style"] = requested_style
                    planning["style_hint"] = requested_style
                job, _effect_spec_payload = build_planned_job(
                    repo_root=repo_root,
                    source_a=source_a,
                    source_b=source_b,
                    mode=forced_mode,
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    frame_count=args.frame_count,
                    output_format="png_sequence",
                    job_name=job_name,
                    reference_transition=None,
                    effect_spec_output=None,
                    planning=planning,
                )
                planning = job.planning
            else:
                sample_hint = analyze_transition(
                    repo_root=repo_root,
                    source_a=source_a,
                    source_b=source_b,
                    input_kind="auto",
                    style_hint=requested_style,
                    intent=None,
                    prefer_generated=False,
                    reference_transition=None,
                    job_name=args.job_name,
                )
                write_json(sample_root / "transition_hint.json", sample_hint)
                planning = build_recommended_plan(
                    repo_root=repo_root,
                    source_a=source_a,
                    source_b=source_b,
                    hint_data={
                        "style_hint": sample_hint.get("style_hint"),
                        "input_kind": sample_hint.get("input_kind"),
                        "job_name": args.job_name,
                    },
                )
                mode = str(planning.get("mode"))
                job_name = args.job_name or str(planning.get("job_name") or "sample_reference")
                job, _effect_spec_payload = build_planned_job(
                    repo_root=repo_root,
                    source_a=source_a,
                    source_b=source_b,
                    mode=mode,
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    frame_count=args.frame_count,
                    output_format="png_sequence",
                    job_name=job_name,
                    reference_transition=None,
                    effect_spec_output=None,
                    planning=planning,
                )
            planning = job.planning
            selected_fx_id = job.effect.fx_id

        write_json(sample_job_output, job.to_dict())
        validation = validate_job(job, repo_root, load_allowed_effects(config_dir))
        if not validation.is_valid:
            raise ValueError("sample-video job did not validate")

        run_result = _execute_job_command(
            repo_root=repo_root,
            harness_root=harness_root,
            config_dir=config_dir,
            job_path=sample_job_output,
            command_name="run",
            renderer=renderer,
            ffmpeg_path=args.ffmpeg,
        )
        demo_video_file = run_result.get("demo_video_file") if isinstance(run_result, dict) else None
        if demo_video_file:
            demo_video_path = Path(str(demo_video_file))
            if demo_video_path.exists():
                output_video.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(demo_video_path, output_video)
        analysis_provider_summary = _build_analysis_provider_artifact_summary(sample_hint)
        report = HarnessReport(
            status="succeeded" if isinstance(run_result, dict) and run_result.get("exit_code") == 0 else "failed",
            summary="sample video rendered" if isinstance(run_result, dict) and run_result.get("exit_code") == 0 else "sample video failed",
            data={
                "sample_root": str(sample_root),
                "sample_context": _build_sample_video_context(
                    source_a=source_a,
                    source_b=source_b,
                    selected_fx_id=selected_fx_id,
                    output_video=output_video,
                    repo_root=repo_root,
                ),
                "workspace_paths": {
                    "sample_root": str(sample_root),
                    "job_file": str(sample_job_output),
                    "report_file": str(sample_report_output),
                    "render_request_file": run_result.get("request_file") if isinstance(run_result, dict) else None,
                    "renderer_result_file": run_result.get("renderer_result_file") if isinstance(run_result, dict) else None,
                    "run_report": run_result.get("report") if isinstance(run_result, dict) else None,
                    "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
                },
                "output_video": str(output_video),
                "selected_fx_id": selected_fx_id,
                "job_file": str(sample_job_output),
                "run_result": run_result,
                "planning": planning,
                "analysis": sample_hint,
                "analysis_provider": sample_hint.get("analysis_provider") if isinstance(sample_hint, dict) else None,
                "analysis_provider_request": analysis_provider_summary["request"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_requested": analysis_provider_summary["requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_selected": analysis_provider_summary["selected"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_delegation": analysis_provider_summary["delegation"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution": analysis_provider_summary["resolution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_requested": analysis_provider_summary["resolution_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_resolved": analysis_provider_summary["resolution_resolved"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_configuration": analysis_provider_summary["resolution_configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration": analysis_provider_summary["configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_status": analysis_provider_summary["resolution_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_reason": analysis_provider_summary["resolution_reason"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_loaded": analysis_provider_summary["configuration_loaded"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_path": analysis_provider_summary["configuration_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_type": analysis_provider_summary["configuration_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_version": analysis_provider_summary["configuration_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_source": analysis_provider_summary["configuration_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_enabled": analysis_provider_summary["configuration_model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_kind": analysis_provider_summary["selected_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_name": analysis_provider_summary["selected_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_selected_provider_mode": analysis_provider_summary["selected_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_contract": analysis_provider_summary["model_execution_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_ready": analysis_provider_summary["model_execution_ready"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_status": analysis_provider_summary["implementation_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_mode": analysis_provider_summary["execution_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_delegation_path": analysis_provider_summary["delegation_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_requested": analysis_provider_summary["model_backed_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_enabled": analysis_provider_summary["model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_source": analysis_provider_summary["analysis_source"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_summary": analysis_provider_summary["transition_summary"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video_analysis": analysis_provider_summary["transition_video_analysis"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video": analysis_provider_summary["transition_video"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_window": analysis_provider_summary["transition_window"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_progression": analysis_provider_summary["transition_progression"] if isinstance(analysis_provider_summary, dict) else None,
                "sample_context": _build_sample_video_context(
                    source_a=source_a,
                    source_b=source_b,
                    selected_fx_id=selected_fx_id,
                    output_video=output_video,
                    repo_root=repo_root,
                ),
            },
        )
        report.write(sample_report_output)
    except Exception as exc:
        flow_error = str(exc)
        report = HarnessReport(
            status="failed",
            summary=f"sample video failed: {flow_error}",
            data={
                "sample_root": str(sample_root),
                "workspace_paths": {
                    "sample_root": str(sample_root),
                    "job_file": str(sample_job_output),
                    "report_file": str(sample_report_output),
                },
                "output_video": str(output_video),
                "selected_fx_id": selected_fx_id,
                "job_file": str(sample_job_output),
                "planning": planning,
                "analysis": sample_hint,
                "analysis_provider": sample_hint.get("analysis_provider") if isinstance(sample_hint, dict) else None,
                "analysis_provider_request": analysis_provider_summary["request"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_requested": analysis_provider_summary["requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_selected": analysis_provider_summary["selected"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_delegation": analysis_provider_summary["delegation"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution": analysis_provider_summary["resolution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_requested": analysis_provider_summary["resolution_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_resolved": analysis_provider_summary["resolution_resolved"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_configuration": analysis_provider_summary["resolution_configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration": analysis_provider_summary["configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_status": analysis_provider_summary["resolution_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_reason": analysis_provider_summary["resolution_reason"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_loaded": analysis_provider_summary["configuration_loaded"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_path": analysis_provider_summary["configuration_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_type": analysis_provider_summary["configuration_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_version": analysis_provider_summary["configuration_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_source": analysis_provider_summary["configuration_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_enabled": analysis_provider_summary["configuration_model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider": analysis_provider_summary["configuration_default_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider": analysis_provider_summary["configuration_model_backed_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_kind": analysis_provider_summary["configuration_default_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_name": analysis_provider_summary["configuration_default_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_mode": analysis_provider_summary["configuration_default_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_kind": analysis_provider_summary["configuration_model_backed_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_name": analysis_provider_summary["configuration_model_backed_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_mode": analysis_provider_summary["configuration_model_backed_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_enabled": analysis_provider_summary["configuration_model_backed_provider_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_source": analysis_provider_summary["configuration_model_backed_provider_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_adapter": analysis_provider_summary["adapter"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_runtime": analysis_provider_summary["runtime"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution": analysis_provider_summary["execution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_request_contract": analysis_provider_summary["model_execution_request_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_result_contract": analysis_provider_summary["model_execution_result_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_type": analysis_provider_summary["execution_contract_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_version": analysis_provider_summary["execution_contract_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_entry_point": analysis_provider_summary["execution_entry_point"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_contract": analysis_provider_summary["model_execution_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_source": analysis_provider_summary["analysis_source"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video": analysis_provider_summary["transition_video"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_window": analysis_provider_summary["transition_window"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_progression": analysis_provider_summary["transition_progression"] if isinstance(analysis_provider_summary, dict) else None,
                "sample_context": _build_sample_video_context(
                    source_a=source_a,
                    source_b=source_b,
                    selected_fx_id=selected_fx_id,
                    output_video=output_video,
                    repo_root=repo_root,
                ),
                "flow_error": flow_error,
            },
        )
        report.write(sample_report_output)

    print(
        json.dumps(
            {
                "sample_report": str(sample_report_output),
                "sample_root": str(sample_root),
                "workspace_paths": {
                    "sample_root": str(sample_root),
                    "job_file": str(sample_job_output),
                    "report_file": str(sample_report_output),
                    "render_request_file": run_result.get("request_file") if isinstance(run_result, dict) else None,
                    "renderer_result_file": run_result.get("renderer_result_file") if isinstance(run_result, dict) else None,
                    "run_report": run_result.get("report") if isinstance(run_result, dict) else None,
                    "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
                },
                "output_video": str(output_video),
                "selected_fx_id": selected_fx_id,
                "sample_context": _build_sample_video_context(
                    source_a=source_a,
                    source_b=source_b,
                    selected_fx_id=selected_fx_id,
                    output_video=output_video,
                    repo_root=repo_root,
                ),
                "analysis_provider": sample_hint.get("analysis_provider") if isinstance(sample_hint, dict) else None,
                "analysis_provider_request": analysis_provider_summary["request"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_requested": analysis_provider_summary["requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_selected": analysis_provider_summary["selected"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_delegation": analysis_provider_summary["delegation"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution": analysis_provider_summary["resolution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_requested": analysis_provider_summary["resolution_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_resolved": analysis_provider_summary["resolution_resolved"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_configuration": analysis_provider_summary["resolution_configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration": analysis_provider_summary["configuration"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_status": analysis_provider_summary["resolution_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_resolution_reason": analysis_provider_summary["resolution_reason"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_loaded": analysis_provider_summary["configuration_loaded"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_path": analysis_provider_summary["configuration_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_version": analysis_provider_summary["configuration_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_source": analysis_provider_summary["configuration_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_enabled": analysis_provider_summary["configuration_model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider": analysis_provider_summary["configuration_default_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider": analysis_provider_summary["configuration_model_backed_provider"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_kind": analysis_provider_summary["configuration_default_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_name": analysis_provider_summary["configuration_default_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_default_provider_mode": analysis_provider_summary["configuration_default_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_kind": analysis_provider_summary["configuration_model_backed_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_name": analysis_provider_summary["configuration_model_backed_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_mode": analysis_provider_summary["configuration_model_backed_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_enabled": analysis_provider_summary["configuration_model_backed_provider_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_configuration_model_backed_provider_source": analysis_provider_summary["configuration_model_backed_provider_source"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_adapter": analysis_provider_summary["adapter"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_runtime": analysis_provider_summary["runtime"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution": analysis_provider_summary["execution"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_request_contract": analysis_provider_summary["model_execution_request_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_result_contract": analysis_provider_summary["model_execution_result_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_type": analysis_provider_summary["execution_contract_type"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_contract_version": analysis_provider_summary["execution_contract_version"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_execution_entry_point": analysis_provider_summary["execution_entry_point"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_contract": analysis_provider_summary["model_execution_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_source": analysis_provider_summary["analysis_source"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video": analysis_provider_summary["transition_video"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_window": analysis_provider_summary["transition_window"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_progression": analysis_provider_summary["transition_progression"] if isinstance(analysis_provider_summary, dict) else None,
                "status": report.status,
                "summary": report.summary,
            },
            indent=2,
        )
    )
    return 0 if report.status == "succeeded" else 1


def _resolve_analysis_output(raw_path: str | None, hint_output: Path) -> Path:
    if raw_path:
        return Path(raw_path).resolve() if Path(raw_path).is_absolute() else Path(raw_path)

    if hint_output.suffix:
        base_name = hint_output.name[: -len(hint_output.suffix)]
    else:
        base_name = hint_output.name
    return hint_output.with_name(f"{base_name}.analysis.json")


def _summarize_reference_transition_window(reference_result) -> dict[str, object | None]:
    if reference_result is None:
        return {
            "frame_count": None,
            "detected_start_frame": None,
            "detected_end_frame": None,
            "detected_frame_count": None,
            "message": None,
        }

    return {
        "frame_count": getattr(reference_result, "frame_count", None),
        "detected_start_frame": getattr(reference_result, "detected_start_frame", None),
        "detected_end_frame": getattr(reference_result, "detected_end_frame", None),
        "detected_frame_count": getattr(reference_result, "detected_frame_count", None),
        "message": getattr(reference_result, "message", None),
    }


def _format_path_for_output(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


def _build_transition_planning_hint(
    transition_video: Path,
    reference_output: Path,
    args,
    repo_root: Path,
) -> dict[str, object | None]:
    inferred_style = _infer_transition_style_from_video_name(transition_video)
    if inferred_style is None and getattr(args, "style_hint", None):
        inferred_style = args.style_hint
    if inferred_style is None and getattr(args, "intent", None):
        inferred_style = _infer_style_from_intent(str(args.intent))

    return {
        "style_hint": inferred_style or "generated-glitch",
        "input_kind": getattr(args, "input_kind", None) or "auto",
        "job_name": getattr(args, "job_name", None),
        "reference_transition": _format_path_for_output(reference_output, repo_root),
        "analysis_source": "transition_video",
        "transition_video": _format_path_for_output(transition_video, repo_root),
    }


def _build_transition_analysis_context(
    transition_video: Path,
    reference_transition: Path | None,
    repo_root: Path,
) -> dict[str, object | None]:
    return {
        "analysis_source": "transition_video",
        "analysis_engine": ANALYSIS_ENGINE,
        "transition_video": _format_path_for_output(transition_video, repo_root),
        "reference_transition": _format_path_for_output(reference_transition, repo_root) if reference_transition is not None else None,
    }


def _build_sample_video_context(
    source_a: Path,
    source_b: Path,
    selected_fx_id: str | None,
    output_video: Path,
    repo_root: Path,
) -> dict[str, object | None]:
    return {
        "input_source": "prepared_sources",
        "source_a": _format_path_for_output(source_a, repo_root),
        "source_b": _format_path_for_output(source_b, repo_root),
        "selected_fx_id": selected_fx_id,
        "output_video": _format_path_for_output(output_video, repo_root),
    }


def _infer_transition_style_from_video_name(transition_video: Path) -> str | None:
    name = transition_video.name.lower()
    if "glitch" in name:
        return "generated-glitch"
    if any(token in name for token in ("seamless", "smooth", "slide")):
        return "generated-seamless"
    if "wipe" in name:
        return "generated-wipe"
    if "dissolve" in name:
        return "generated-dissolve"
    if "mask" in name:
        return "generated-mask"
    if "rgb" in name:
        return "generated-rgb-split"
    if "noise" in name:
        return "generated-noise"
    return None


def _infer_style_from_intent(intent: str) -> str | None:
    normalized = intent.lower()
    if "glitch" in normalized:
        return "generated-glitch"
    if any(token in normalized for token in ("seamless", "smooth", "slide")):
        return "generated-seamless"
    if "wipe" in normalized:
        return "generated-wipe"
    if "dissolve" in normalized:
        return "generated-dissolve"
    if "mask" in normalized:
        return "generated-mask"
    if "rgb" in normalized:
        return "generated-rgb-split"
    if "noise" in normalized:
        return "generated-noise"
    return None


def _handle_plan_job(args, repo_root: Path, config_dir: Path) -> int:
    analysis_data: dict | None = None
    hint_data: dict | None = None
    comparison_output: Path | None = None
    if args.hint_file and args.analysis_file:
        print("plan-job failed: use either --hint-file or --analysis-file, not both")
        return 1
    if args.comparison_output and not args.analysis_file:
        print("plan-job failed: --comparison-output requires --analysis-file")
        return 1
    if args.comparison_output:
        comparison_output = _resolve_path_argument(args.comparison_output, repo_root)

    if args.hint_file:
        hint_path = _resolve_path_argument(args.hint_file, repo_root)
        try:
            hint_data = load_transition_hint(hint_path)
        except Exception as exc:
            print(f"plan-job failed: could not load hint file: {exc}")
            return 1
    elif args.analysis_file:
        analysis_path = _resolve_path_argument(args.analysis_file, repo_root)
        try:
            analysis_data = load_transition_analysis(analysis_path)
            hint_data = extract_hint_from_analysis(analysis_data)
        except Exception as exc:
            print(f"plan-job failed: could not load analysis file: {exc}")
            return 1

    hint_preset = hint_data.get("preset") if hint_data else None
    hint_style = hint_data.get("style_hint") if hint_data else None
    hint_input_kind = hint_data.get("input_kind") if hint_data else None
    hint_reference_transition = hint_data.get("reference_transition") if hint_data else None
    hint_job_name = hint_data.get("job_name") if hint_data else None
    analysis_recommended_plan = extract_plan_from_analysis(analysis_data) if analysis_data else None
    recomputed_plan: dict | None = None
    analysis_source_a = None
    analysis_source_b = None
    analysis_reference_transition = None
    if analysis_data:
        analysis_source_a, analysis_source_b, analysis_reference_transition = extract_sources_from_analysis(
            analysis_data
        )

    if args.recompute_plan_from_facts:
        if not analysis_data:
            print("plan-job failed: --recompute-plan-from-facts requires --analysis-file")
            return 1
        resolved_facts = extract_resolved_facts_from_analysis(analysis_data)
        if not resolved_facts:
            print("plan-job failed: analysis artifact does not contain facts.resolved for recompute mode")
            return 1
        if not analysis_source_a or not analysis_source_b:
            print("plan-job failed: analysis artifact does not contain source paths for recompute mode")
            return 1

        recompute_hint = {
            "style_hint": resolved_facts.get("style_hint"),
            "input_kind": resolved_facts.get("input_kind"),
            "job_name": resolved_facts.get("job_name"),
            "reference_transition": analysis_reference_transition,
        }
        try:
            recomputed_plan = build_recommended_plan(
                repo_root=repo_root,
                source_a=_resolve_path_argument(str(analysis_source_a), repo_root),
                source_b=_resolve_path_argument(str(analysis_source_b), repo_root),
                hint_data=recompute_hint,
            )
        except Exception as exc:
            print(f"plan-job failed: could not recompute plan from facts: {exc}")
            return 1
        analysis_recommended_plan = recomputed_plan

    preset_name = args.preset
    if not preset_name and analysis_recommended_plan and analysis_recommended_plan.get("preset"):
        preset_name = str(analysis_recommended_plan.get("preset"))
    if not preset_name and hint_preset:
        preset_name = hint_preset
    preset = planner_preset(preset_name) if preset_name else {}

    source_a_for_auto = None
    source_b_for_auto = None
    auto_input_kind = None
    auto_mode = None
    planning_metadata: dict | None = analysis_recommended_plan

    auto_requested = args.auto or bool(analysis_recommended_plan) or bool(hint_style)

    if auto_requested:
        effective_style = args.style or (
            str(analysis_recommended_plan.get("style"))
            if analysis_recommended_plan and analysis_recommended_plan.get("style")
            else None
        ) or hint_style
        effective_input_kind = (
            str(analysis_recommended_plan.get("input_kind"))
            if analysis_recommended_plan and analysis_recommended_plan.get("input_kind")
            else hint_input_kind or args.input_kind
        )

        if not effective_style:
            print("plan-job failed: --style is required when --auto is used")
            return 1
        source_a_auto_raw = args.source_a or analysis_source_a
        source_b_auto_raw = args.source_b or analysis_source_b
        if not source_a_auto_raw or not source_b_auto_raw:
            print("plan-job failed: --source-a and --source-b are required when --auto is used")
            return 1

        source_a_for_auto = _resolve_path_argument(str(source_a_auto_raw), repo_root)
        source_b_for_auto = _resolve_path_argument(str(source_b_auto_raw), repo_root)
        auto_preset_name, auto_mode, auto_input_kind = resolve_auto_plan(
            repo_root=repo_root,
            source_a=source_a_for_auto,
            source_b=source_b_for_auto,
            style=effective_style,
            input_kind=effective_input_kind,
        )
        if auto_preset_name:
            preset_name = auto_preset_name
            preset = planner_preset(preset_name)

    source_a_raw = args.source_a or analysis_source_a or preset.get("source_a")
    source_b_raw = args.source_b or analysis_source_b or preset.get("source_b")
    job_output_raw = args.job_output or preset.get("job_output")
    mode = args.mode or auto_mode or (
        str(analysis_recommended_plan.get("mode"))
        if analysis_recommended_plan and analysis_recommended_plan.get("mode")
        else None
    ) or preset.get("mode")
    job_name = args.job_name or hint_job_name or preset.get("job_name")
    effect_spec_output_raw = args.effect_spec_output or preset.get("effect_spec_output")

    missing_fields = [
        field_name
        for field_name, field_value in {
            "source_a": source_a_raw,
            "source_b": source_b_raw,
            "job_output": job_output_raw,
            "mode": mode,
        }.items()
        if not field_value
    ]
    if missing_fields:
        print(
            "plan-job failed: missing required arguments after preset resolution: "
            + ", ".join(missing_fields)
        )
        return 1

    source_a = _resolve_path_argument(str(source_a_raw), repo_root)
    source_b = _resolve_path_argument(str(source_b_raw), repo_root)
    job_output = _resolve_path_argument(str(job_output_raw), repo_root)
    effect_spec_output = (
        _resolve_path_argument(str(effect_spec_output_raw), repo_root)
        if effect_spec_output_raw
        else None
    )
    reference_transition = (
        _resolve_path_argument(args.reference_transition, repo_root)
        if args.reference_transition
        else _resolve_path_argument(str(analysis_reference_transition), repo_root)
        if analysis_reference_transition
        else _resolve_path_argument(str(hint_reference_transition), repo_root)
        if hint_reference_transition
        else None
    )
    if planning_metadata is None and auto_requested:
        effective_style = args.style or (
            str(analysis_recommended_plan.get("style"))
            if analysis_recommended_plan and analysis_recommended_plan.get("style")
            else None
        ) or hint_style
        effective_input_kind = (
            str(analysis_recommended_plan.get("input_kind"))
            if analysis_recommended_plan and analysis_recommended_plan.get("input_kind")
            else hint_input_kind or args.input_kind
        )
        planning_metadata = build_recommended_plan(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            hint_data={
                "style_hint": effective_style,
                "input_kind": effective_input_kind,
                "job_name": job_name,
                "reference_transition": _format_path_for_output(reference_transition, repo_root)
                if reference_transition is not None
                else None,
            },
        )
    resolved_frame_count = None
    frame_count_source = None

    try:
        resolved_frame_count, frame_count_source = resolve_planned_frame_count(
            reference_transition=reference_transition,
            explicit_frame_count=args.frame_count,
        )
        job, effect_spec_payload = build_planned_job(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            mode=str(mode),
            width=args.width,
            height=args.height,
            fps=args.fps,
            frame_count=resolved_frame_count,
            output_format=args.output_format,
            job_name=job_name,
            reference_transition=reference_transition,
            effect_spec_output=effect_spec_output,
            planning=planning_metadata,
        )
        planning_metadata = job.planning

        if effect_spec_output is not None and effect_spec_payload is not None:
            write_json(effect_spec_output, effect_spec_payload)

        write_json(job_output, job.to_dict())

        validation = validate_job(job, repo_root, load_allowed_effects(config_dir))
    except Exception as exc:
        print(f"plan-job failed: {exc}")
        return 1

    result = {
        "job_output": str(job_output),
        "mode": mode,
        "preset": preset_name,
        "auto": auto_requested,
        "style": args.style or (
            str(analysis_recommended_plan.get("style"))
            if analysis_recommended_plan and analysis_recommended_plan.get("style")
            else hint_style
        ),
        "input_kind": auto_input_kind or hint_input_kind or args.input_kind,
        "hint_file": args.hint_file,
        "analysis_file": args.analysis_file,
        "plan_source": "recomputed_from_facts" if args.recompute_plan_from_facts else "analysis_embedded_or_hint",
        "job_name": job.job_name,
        "frame_count": job.render.frame_count,
        "frame_count_source": frame_count_source,
        "validation_valid": validation.is_valid,
        "planning": planning_metadata,
        "planning_retrieval_summary": _summarize_retrieval_fields(planning_metadata),
        "issues": [
            {"field": issue.field, "level": issue.level, "message": issue.message}
            for issue in validation.issues
        ],
    }
    embedded_plan = extract_plan_from_analysis(analysis_data) if analysis_data else None
    if embedded_plan is not None:
        result["embedded_plan"] = embedded_plan
        result["embedded_plan_summary"] = _summarize_plan_fields(embedded_plan)
        result["embedded_plan_retrieval_summary"] = _summarize_retrieval_fields(embedded_plan)
    if recomputed_plan is not None:
        result["recomputed_plan"] = recomputed_plan
        result["recomputed_plan_summary"] = _summarize_plan_fields(recomputed_plan)
        result["recomputed_plan_retrieval_summary"] = _summarize_retrieval_fields(recomputed_plan)
        if embedded_plan is not None:
            result["recompute_matches_embedded"] = (
                _summarize_plan_fields(embedded_plan) == _summarize_plan_fields(recomputed_plan)
            )
    if effect_spec_output is not None and effect_spec_payload is not None:
        result["effect_spec_output"] = str(effect_spec_output)

    if comparison_output is not None:
        comparison_report = _build_plan_comparison_report(
            analysis_file=args.analysis_file,
            job_output=job_output,
            plan_source=result["plan_source"],
            selected_plan={
                "auto": auto_requested,
                "style": result["style"],
                "input_kind": result["input_kind"],
                "preset": preset_name,
                "mode": mode,
                "job_name": job.job_name,
            },
            selected_plan_retrieval_summary=result.get("planning_retrieval_summary"),
            embedded_plan=embedded_plan,
            embedded_plan_summary=result.get("embedded_plan_summary"),
            recomputed_plan=recomputed_plan,
            recomputed_plan_summary=result.get("recomputed_plan_summary"),
            recompute_matches_embedded=result.get("recompute_matches_embedded"),
            validation_valid=validation.is_valid,
            issues=result["issues"],
        )
        write_json(comparison_output, comparison_report)
        result["comparison_output"] = str(comparison_output)

    print(json.dumps(result, indent=2))
    return 0 if validation.is_valid else 1


def _summarize_plan_fields(plan_data: dict) -> dict[str, str | bool | None]:
    return {
        "auto": bool(plan_data.get("auto")),
        "style": str(plan_data.get("style")) if plan_data.get("style") is not None else None,
        "input_kind": str(plan_data.get("input_kind")) if plan_data.get("input_kind") is not None else None,
        "preset": str(plan_data.get("preset")) if plan_data.get("preset") is not None else None,
        "mode": str(plan_data.get("mode")) if plan_data.get("mode") is not None else None,
        "job_name": str(plan_data.get("job_name")) if plan_data.get("job_name") is not None else None,
    }


def _summarize_retrieval_fields(plan_data: dict | None) -> dict[str, object | None] | None:
    if not isinstance(plan_data, dict):
        return None

    retrieval = plan_data.get("retrieval")
    if not isinstance(retrieval, dict):
        return None

    return {
        "status": retrieval.get("status"),
        "effect_id": retrieval.get("effect_id"),
        "mode": retrieval.get("mode"),
        "fallback_used": retrieval.get("fallback_used"),
        "fallback_mode": retrieval.get("fallback_mode"),
        "fallback_preset": retrieval.get("fallback_preset"),
        "fallback_reason": retrieval.get("fallback_reason"),
        "match_kind": retrieval.get("match_kind"),
        "matched_style_hint": retrieval.get("matched_style_hint"),
        "candidate_count": retrieval.get("candidate_count"),
    }


def _build_analysis_provider_artifact_summary(analysis_artifact: dict | None) -> dict[str, Any] | None:
    if not isinstance(analysis_artifact, dict):
        return None

    facts = analysis_artifact.get("facts")
    if not isinstance(facts, dict):
        return None

    resolution = facts.get("analysis_provider_resolution")
    runtime = facts.get("analysis_provider_runtime")
    execution = runtime.get("execution") if isinstance(runtime, dict) else None
    delegation = runtime.get("delegation") if isinstance(runtime, dict) else None
    configuration = resolution.get("configuration") if isinstance(resolution, dict) else None
    transition_video_analysis = facts.get("transition_video_analysis")

    return {
        "request": facts.get("analysis_provider_request"),
        "requested": runtime.get("requested") if isinstance(runtime, dict) else None,
        "selected": runtime.get("selected") if isinstance(runtime, dict) else None,
        "delegation": delegation,
        "resolution": resolution,
        "resolution_requested": resolution.get("requested") if isinstance(resolution, dict) else None,
        "resolution_resolved": resolution.get("resolved") if isinstance(resolution, dict) else None,
        "resolution_configuration": configuration,
        "configuration": configuration,
        "runtime": runtime,
        "execution": execution,
        "execution_contract_type": execution.get("contract_type") if isinstance(execution, dict) else None,
        "execution_contract_version": execution.get("contract_version") if isinstance(execution, dict) else None,
        "execution_entry_point": execution.get("entry_point") if isinstance(execution, dict) else None,
        "resolution_status": resolution.get("status") if isinstance(resolution, dict) else None,
        "resolution_reason": resolution.get("reason") if isinstance(resolution, dict) else None,
        "configuration_loaded": resolution.get("configuration", {}).get("loaded") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_path": resolution.get("configuration", {}).get("config_path") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_type": resolution.get("configuration", {}).get("config_type") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_version": resolution.get("configuration", {}).get("config_version") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_source": resolution.get("configuration", {}).get("config_source") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_model_backed_enabled": resolution.get("configuration", {}).get("model_backed_enabled") if isinstance(resolution, dict) and isinstance(resolution.get("configuration"), dict) else None,
        "configuration_default_provider": configuration.get("default_provider") if isinstance(configuration, dict) else None,
        "configuration_model_backed_provider": configuration.get("model_backed_provider") if isinstance(configuration, dict) else None,
        "configuration_default_provider_kind": configuration.get("default_provider", {}).get("kind") if isinstance(configuration, dict) and isinstance(configuration.get("default_provider"), dict) else None,
        "configuration_default_provider_name": configuration.get("default_provider", {}).get("name") if isinstance(configuration, dict) and isinstance(configuration.get("default_provider"), dict) else None,
        "configuration_default_provider_mode": configuration.get("default_provider", {}).get("mode") if isinstance(configuration, dict) and isinstance(configuration.get("default_provider"), dict) else None,
        "configuration_model_backed_provider_kind": configuration.get("model_backed_provider", {}).get("kind") if isinstance(configuration, dict) and isinstance(configuration.get("model_backed_provider"), dict) else None,
        "configuration_model_backed_provider_name": configuration.get("model_backed_provider", {}).get("name") if isinstance(configuration, dict) and isinstance(configuration.get("model_backed_provider"), dict) else None,
        "configuration_model_backed_provider_mode": configuration.get("model_backed_provider", {}).get("mode") if isinstance(configuration, dict) and isinstance(configuration.get("model_backed_provider"), dict) else None,
        "configuration_model_backed_provider_enabled": configuration.get("model_backed_provider", {}).get("enabled") if isinstance(configuration, dict) and isinstance(configuration.get("model_backed_provider"), dict) else None,
        "configuration_model_backed_provider_source": configuration.get("model_backed_provider", {}).get("source") if isinstance(configuration, dict) and isinstance(configuration.get("model_backed_provider"), dict) else None,
        "selected_provider_kind": resolution.get("resolved", {}).get("kind") if isinstance(resolution, dict) else None,
        "selected_provider_name": resolution.get("resolved", {}).get("name") if isinstance(resolution, dict) else None,
        "selected_provider_mode": resolution.get("resolved", {}).get("mode") if isinstance(resolution, dict) else None,
        "model_execution_ready": runtime.get("delegation", {}).get("model_execution_ready") if isinstance(runtime, dict) else None,
        "implementation_status": execution.get("implementation_status") if isinstance(execution, dict) else None,
        "execution_mode": execution.get("execution_mode") if isinstance(execution, dict) else None,
        "delegation_path": delegation.get("path") if isinstance(delegation, dict) else None,
        "model_backed_requested": delegation.get("model_backed_requested") if isinstance(delegation, dict) else None,
        "model_backed_enabled": delegation.get("model_backed_enabled") if isinstance(delegation, dict) else None,
        "adapter": runtime.get("adapter") if isinstance(runtime, dict) else None,
        "model_execution_contract": execution.get("model_execution_contract") if isinstance(execution, dict) else None,
        "model_execution_request_contract": execution.get("model_execution_contract", {}).get("request_contract") if isinstance(execution, dict) and isinstance(execution.get("model_execution_contract"), dict) else None,
        "model_execution_result_contract": execution.get("model_execution_contract", {}).get("result_contract") if isinstance(execution, dict) and isinstance(execution.get("model_execution_contract"), dict) else None,
        "analysis_source": facts.get("analysis_source"),
        "transition_summary": facts.get("transition_summary"),
        "transition_video_analysis": transition_video_analysis,
        "transition_video": transition_video_analysis.get("transition_video") if isinstance(transition_video_analysis, dict) else None,
        "transition_window": transition_video_analysis.get("transition_window") if isinstance(transition_video_analysis, dict) else None,
        "transition_progression": transition_video_analysis.get("transition_progression") if isinstance(transition_video_analysis, dict) else None,
    }


def _build_flow_report(
    repo_root: Path,
    flow_root: Path,
    transition_video: Path,
    source_a: Path,
    source_b: Path,
    reference_result,
    hint_output: Path,
    analysis_output: Path,
    analysis_artifact: dict | None,
    planning: dict | None,
    job_output: Path,
    job,
    validation,
    run_result: dict | None,
    effect_spec_output: Path | None,
    flow_error: str | None,
) -> HarnessReport:
    validation_valid = validation.is_valid if validation is not None else None
    validation_issues = []
    if validation is not None:
        validation_issues = [
            {"field": issue.field, "level": issue.level, "message": issue.message}
            for issue in validation.issues
        ]

    planning_retrieval_summary = _summarize_retrieval_fields(planning)
    run_status = run_result.get("status") if isinstance(run_result, dict) else None
    run_summary = run_result.get("summary") if isinstance(run_result, dict) else None
    run_evaluation = run_result.get("evaluation") if isinstance(run_result, dict) else None
    analysis_provider_summary = _build_analysis_provider_artifact_summary(analysis_artifact)

    status = _resolve_flow_status(flow_error, validation_valid, run_status)
    summary = _resolve_flow_summary(flow_error, validation_valid, run_summary, reference_result)

    return HarnessReport(
        status=status,
        summary=summary,
        report_type="flow_report",
        data={
            "flow_root": str(flow_root),
            "analysis_context": _build_transition_analysis_context(
                transition_video=transition_video,
                reference_transition=reference_result.output_dir if reference_result is not None else None,
                repo_root=repo_root,
            ),
            "workspace_paths": {
                "flow_root": str(flow_root),
                "reference_transition_dir": str(reference_result.output_dir) if reference_result is not None else None,
                "reference_transition_manifest": str(reference_result.manifest_file) if reference_result is not None else None,
                "hint_file": str(hint_output),
                "analysis_file": str(analysis_output),
                "job_file": str(job_output) if job_output is not None else None,
                "effect_spec_file": str(effect_spec_output) if effect_spec_output is not None else None,
                "similarity_report_file": run_result.get("evaluation", {}).get("score", {}).get("report_file")
                if isinstance(run_result, dict)
                else None,
                "render_request_file": run_result.get("request_file") if isinstance(run_result, dict) else None,
                "renderer_result_file": run_result.get("renderer_result_file") if isinstance(run_result, dict) else None,
                "run_report": run_result.get("report") if isinstance(run_result, dict) else None,
                "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
            },
            "inputs": {
                "transition_video": str(transition_video),
                "source_a": str(source_a),
                "source_b": str(source_b),
            },
            "artifacts": {
                "reference_transition_dir": str(reference_result.output_dir) if reference_result is not None else None,
                "reference_transition_manifest": str(reference_result.manifest_file) if reference_result is not None else None,
                "hint_file": str(hint_output),
                "analysis_file": str(analysis_output),
                "analysis_artifact": analysis_artifact,
                "job_file": str(job_output) if job_output is not None else None,
                "effect_spec_file": str(effect_spec_output) if effect_spec_output is not None else None,
                "similarity_report_file": run_result.get("evaluation", {}).get("score", {}).get("report_file")
                if isinstance(run_result, dict)
                else None,
                "render_request_file": run_result.get("request_file") if isinstance(run_result, dict) else None,
                "renderer_result_file": run_result.get("renderer_result_file") if isinstance(run_result, dict) else None,
                "run_report": run_result.get("report") if isinstance(run_result, dict) else None,
                "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
            },
            "reference_transition": (
                {
                    "frame_count": reference_result.frame_count,
                    "message": reference_result.message,
                    "detected_start_frame": reference_result.detected_start_frame,
                    "detected_end_frame": reference_result.detected_end_frame,
                    "detected_frame_count": reference_result.detected_frame_count,
                    "manifest_file": str(reference_result.manifest_file),
                }
                if reference_result is not None
                else None
            ),
            "planning": {
                "job_name": job.job_name if job is not None else None,
                "mode": planning.get("mode") if isinstance(planning, dict) else None,
                "preset": planning.get("preset") if isinstance(planning, dict) else None,
                "retrieval_summary": planning_retrieval_summary,
                "validation_valid": validation_valid,
                "issues": validation_issues,
            },
            "analysis_artifact": analysis_artifact,
            "analysis_provider_request": analysis_provider_summary["request"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_requested": analysis_provider_summary["requested"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_selected": analysis_provider_summary["selected"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_delegation": analysis_provider_summary["delegation"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution": analysis_provider_summary["resolution"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution_requested": analysis_provider_summary["resolution_requested"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution_resolved": analysis_provider_summary["resolution_resolved"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution_configuration": analysis_provider_summary["resolution_configuration"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration": analysis_provider_summary["configuration"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution_status": analysis_provider_summary["resolution_status"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_resolution_reason": analysis_provider_summary["resolution_reason"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_loaded": analysis_provider_summary["configuration_loaded"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_path": analysis_provider_summary["configuration_path"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_type": analysis_provider_summary["configuration_type"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_version": analysis_provider_summary["configuration_version"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_source": analysis_provider_summary["configuration_source"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_enabled": analysis_provider_summary["configuration_model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_default_provider": analysis_provider_summary["configuration_default_provider"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider": analysis_provider_summary["configuration_model_backed_provider"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_default_provider_kind": analysis_provider_summary["configuration_default_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_default_provider_name": analysis_provider_summary["configuration_default_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_default_provider_mode": analysis_provider_summary["configuration_default_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider_kind": analysis_provider_summary["configuration_model_backed_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider_name": analysis_provider_summary["configuration_model_backed_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider_mode": analysis_provider_summary["configuration_model_backed_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider_enabled": analysis_provider_summary["configuration_model_backed_provider_enabled"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_configuration_model_backed_provider_source": analysis_provider_summary["configuration_model_backed_provider_source"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_adapter": analysis_provider_summary["adapter"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_runtime": analysis_provider_summary["runtime"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution": analysis_provider_summary["execution"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution_request_contract": analysis_provider_summary["model_execution_request_contract"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution_result_contract": analysis_provider_summary["model_execution_result_contract"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution_contract_type": analysis_provider_summary["execution_contract_type"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution_contract_version": analysis_provider_summary["execution_contract_version"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_provider_execution_entry_point": analysis_provider_summary["execution_entry_point"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_selected_provider_kind": analysis_provider_summary["selected_provider_kind"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_selected_provider_name": analysis_provider_summary["selected_provider_name"] if isinstance(analysis_provider_summary, dict) else None,
            "analysis_selected_provider_mode": analysis_provider_summary["selected_provider_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_provider_adapter": analysis_provider_summary["adapter"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_contract": analysis_provider_summary["model_execution_contract"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_ready": analysis_provider_summary["model_execution_ready"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_status": analysis_provider_summary["implementation_status"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_execution_mode": analysis_provider_summary["execution_mode"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_delegation_path": analysis_provider_summary["delegation_path"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_requested": analysis_provider_summary["model_backed_requested"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_model_backed_enabled": analysis_provider_summary["model_backed_enabled"] if isinstance(analysis_provider_summary, dict) else None,
                "analysis_source": analysis_provider_summary["analysis_source"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_summary": analysis_provider_summary["transition_summary"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video_analysis": analysis_provider_summary["transition_video_analysis"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_video": analysis_provider_summary["transition_video"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_window": analysis_provider_summary["transition_window"] if isinstance(analysis_provider_summary, dict) else None,
                "transition_progression": analysis_provider_summary["transition_progression"] if isinstance(analysis_provider_summary, dict) else None,
            "run": {
                "status": run_status,
                "summary": run_summary,
                "demo_video_file": run_result.get("demo_video_file") if isinstance(run_result, dict) else None,
                "demo_video_result": run_result.get("demo_video_result") if isinstance(run_result, dict) else None,
                "evaluation": run_evaluation,
            },
            "flow_error": flow_error,
        },
    )


def _resolve_flow_status(flow_error: str | None, validation_valid: bool | None, run_status: str | None) -> str:
    if flow_error is not None:
        return "failed"
    if validation_valid is False:
        return "blocked"
    if run_status in {"succeeded", "blocked"}:
        return run_status
    if run_status is None:
        return "blocked" if validation_valid else "failed"
    return "failed"


def _resolve_flow_summary(
    flow_error: str | None,
    validation_valid: bool | None,
    run_summary: str | None,
    reference_result,
) -> str:
    if flow_error is not None:
        return f"end-to-end flow failed: {flow_error}"
    if validation_valid is False:
        return "end-to-end flow blocked by validation"
    if run_summary is not None:
        return run_summary
    if reference_result is not None:
        return "end-to-end flow completed reference preparation and planning"
    return "end-to-end flow did not start"


def _slugify_text(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    normalized = normalized.strip("_")
    return normalized or "sample_reference"


def _build_plan_comparison_report(
    analysis_file: str | None,
    job_output: Path | None,
    plan_source: str,
    selected_plan: dict[str, str | bool | None],
    selected_plan_retrieval_summary: dict[str, object | None] | None,
    embedded_plan: dict | None,
    embedded_plan_summary: dict[str, str | bool | None] | None,
    recomputed_plan: dict | None,
    recomputed_plan_summary: dict[str, str | bool | None] | None,
    recompute_matches_embedded: bool | None,
    validation_valid: bool,
    issues: list[dict],
) -> dict[str, object]:
    return {
        "report_type": "plan_comparison",
        "report_version": 1,
        "analysis_file": analysis_file,
        "job_output": str(job_output) if job_output is not None else None,
        "plan_source": plan_source,
        "selected_plan": selected_plan,
        "selected_plan_retrieval_summary": selected_plan_retrieval_summary,
        "embedded_plan": embedded_plan,
        "embedded_plan_summary": embedded_plan_summary,
        "embedded_plan_retrieval_summary": _summarize_retrieval_fields(embedded_plan),
        "recomputed_plan": recomputed_plan,
        "recomputed_plan_summary": recomputed_plan_summary,
        "recomputed_plan_retrieval_summary": _summarize_retrieval_fields(recomputed_plan),
        "recompute_matches_embedded": recompute_matches_embedded,
        "validation_valid": validation_valid,
        "issues": issues,
    }


def _handle_smoke_test(
    args,
    repo_root: Path,
    harness_root: Path,
    config_dir: Path,
    default_renderer: str | None,
) -> int:
    return _run_smoke_test_suite(
        args=args,
        repo_root=repo_root,
        harness_root=harness_root,
        config_dir=config_dir,
        default_renderer=default_renderer,
        suite_name="smoke_test",
        job_paths=OFFICIAL_SMOKE_TEST_JOBS,
    )


def _handle_real_smoke_test(
    args,
    repo_root: Path,
    harness_root: Path,
    config_dir: Path,
    default_renderer: str | None,
) -> int:
    return _run_smoke_test_suite(
        args=args,
        repo_root=repo_root,
        harness_root=harness_root,
        config_dir=config_dir,
        default_renderer=default_renderer,
        suite_name="real_smoke_test",
        job_paths=OFFICIAL_REAL_SMOKE_TEST_JOBS,
    )


def _run_smoke_test_suite(
    args,
    repo_root: Path,
    harness_root: Path,
    config_dir: Path,
    default_renderer: str | None,
    suite_name: str,
    job_paths: tuple[str, ...],
) -> int:
    smoke_test_root = harness_root / "work" / f"{suite_name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    smoke_test_root.mkdir(parents=True, exist_ok=False)

    results: list[dict] = []
    overall_exit_code = 0
    renderer = _resolve_renderer_argument(args.renderer, default_renderer)

    for relative_job_path in job_paths:
        job_path = (repo_root / relative_job_path).resolve()
        validation_result = _execute_job_command(
            repo_root=repo_root,
            harness_root=harness_root,
            config_dir=config_dir,
            job_path=job_path,
            command_name="validate",
        )

        job_result = {
            "job": relative_job_path,
            "validate_exit_code": validation_result["exit_code"],
            "validation_valid": validation_result["validation_valid"],
            "validate_retrieval_summary": validation_result.get("planning_retrieval_summary"),
        }

        if validation_result["exit_code"] != 0:
            overall_exit_code = 1
            results.append(job_result)
            continue

        if renderer:
            run_result = _execute_job_command(
                repo_root=repo_root,
                harness_root=harness_root,
                config_dir=config_dir,
                job_path=job_path,
                command_name="run",
                renderer=renderer,
            )
            job_result.update(
                {
                    "run_exit_code": run_result["exit_code"],
                    "run_status": run_result.get("status"),
                    "run_summary": run_result.get("summary"),
                    "workspace": run_result.get("workspace"),
                    "report": run_result.get("report"),
                    "run_evaluation": run_result.get("evaluation"),
                    "run_retrieval_summary": _summarize_retrieval_from_evaluation(run_result.get("evaluation")),
                }
            )
            if run_result["exit_code"] != 0:
                overall_exit_code = 1
        else:
            job_result.update(
                {
                    "run_status": "not-run",
                    "run_summary": "renderer not provided; smoke test performed validation only",
                }
            )

        results.append(job_result)

    retrieval_summary = _summarize_smoke_test_retrieval(results)
    summary = {
        "status": "succeeded" if overall_exit_code == 0 else "failed",
        "suite": suite_name,
        "renderer": renderer,
        "retrieval_summary": retrieval_summary,
        "validation_retrieval_summary": _summarize_smoke_test_validation_retrieval(results),
        "results": results,
    }
    summary_path = smoke_test_root / "smoke_test_report.json"
    write_json(summary_path, summary)

    print(
        json.dumps(
            {
                "smoke_test_report": str(summary_path),
                "retrieval_summary": retrieval_summary,
                "validation_retrieval_summary": _summarize_smoke_test_validation_retrieval(results),
                "results": results,
            },
            indent=2,
        )
    )
    return overall_exit_code


def _summarize_smoke_test_retrieval(results: list[dict]) -> dict[str, int] | None:
    return _summarize_smoke_test_retrieval_for_key(results, "run_retrieval_summary")


def _summarize_smoke_test_validation_retrieval(results: list[dict]) -> dict[str, int] | None:
    return _summarize_smoke_test_retrieval_for_key(results, "validate_retrieval_summary")


def _summarize_smoke_test_retrieval_for_key(results: list[dict], retrieval_key: str) -> dict[str, int] | None:
    if not results:
        return None

    summary = {
        "job_count": 0,
        "retrieved_count": 0,
        "not_found_count": 0,
        "fallback_used_count": 0,
    }
    for job_result in results:
        if not isinstance(job_result, dict):
            continue
        summary["job_count"] += 1
        retrieval = job_result.get(retrieval_key)
        if not isinstance(retrieval, dict):
            continue
        status = retrieval.get("status")
        if status == "retrieved":
            summary["retrieved_count"] += 1
        elif status == "not_found":
            summary["not_found_count"] += 1
        if retrieval.get("fallback_used"):
            summary["fallback_used_count"] += 1

    return summary


def _summarize_retrieval_from_evaluation(evaluation: dict | None) -> dict[str, object | None] | None:
    if not isinstance(evaluation, dict):
        return None

    planning = evaluation.get("planning")
    if not isinstance(planning, dict):
        return None

    return {
        "status": planning.get("retrieval_status"),
        "effect_id": planning.get("retrieval_effect_id"),
        "mode": planning.get("retrieval_mode"),
        "fallback_used": planning.get("retrieval_fallback_used"),
        "fallback_mode": planning.get("retrieval_fallback_mode"),
        "fallback_preset": planning.get("retrieval_fallback_preset"),
        "fallback_reason": planning.get("retrieval_fallback_reason"),
        "match_kind": planning.get("retrieval_match_kind"),
        "matched_style_hint": planning.get("retrieval_matched_style_hint"),
        "candidate_count": planning.get("retrieval_candidate_count"),
    }


def _resolve_path_argument(raw_path: str, repo_root: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def _resolve_default_renderer(repo_root: Path) -> str | None:
    renderer_path = (repo_root / DEFAULT_RENDERER_RELATIVE_PATH).resolve()
    if renderer_path.exists():
        return str(renderer_path)
    return None


def _resolve_renderer_argument(renderer: str | None, default_renderer: str | None) -> str | None:
    if renderer:
        return renderer
    return default_renderer


def _handle_validate(validation) -> int:
    _print_validation(validation)
    return 0 if validation.is_valid else 1


def _print_validation(validation) -> None:
    if not validation.issues:
        print("Validation passed")
        return

    for issue in validation.issues:
        print(f"[{issue.level}] {issue.field}: {issue.message}")


if __name__ == "__main__":
    sys.exit(main())
