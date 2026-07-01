from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from .config import load_allowed_effects, load_eval_thresholds
from .evaluator import score_frame_sequences
from .models import load_render_job
from .analyzer import analyze_transition
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
    planner_modes,
    planner_preset,
    planner_presets,
    resolve_planned_frame_count,
    resolve_auto_plan,
)
from .renderer import prepare_render_invocation
from .report import HarnessReport
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
    if args.command == "plan-job":
        return _handle_plan_job(args, repo_root, config_dir)
    if args.command == "smoke-test":
        return _handle_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "real-smoke-test":
        return _handle_real_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "score":
        return _handle_score(args, repo_root)

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

    print(json.dumps({"workspace": result["workspace"], "report": result["report"]}, indent=2))
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
        }

    if not validation.is_valid:
        _print_validation(validation)
        return {
            "exit_code": 1,
            "validation_valid": False,
            "job_path": str(job_path),
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
        }

    invocation = prepare_render_invocation(repo_root, workspace, job, renderer)
    similarity_report: dict | None = None
    similarity_report_file: Path | None = None
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

    report = HarnessReport(
        status=invocation.status,
        summary=invocation.message,
        data={
            "workspace": str(workspace.root),
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
            "similarity_report_file": str(similarity_report_file) if similarity_report_file is not None else None,
            "similarity_report": similarity_report,
        },
    )
    report_path = workspace.reports_dir / "run_report.json"
    report.write(report_path)

    return {
        "exit_code": 0 if invocation.status in {"succeeded", "blocked"} else 1,
        "validation_valid": True,
        "job_path": str(job_path),
        "workspace": str(workspace.root),
        "report": str(report_path),
        "status": invocation.status,
        "summary": invocation.message,
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
    similarity_report = {
        "report_type": "similarity_score",
        "report_version": 1,
        "candidate": _format_path_for_output(candidate, repo_root),
        "reference": _format_path_for_output(reference, repo_root),
        "status": "succeeded",
        "alignment": _build_similarity_alignment(
            repo_root=repo_root,
            reference=reference,
            expected_frame_count=expected_frame_count,
            score=score,
            reference_manifest=reference_manifest,
        ),
        "score": score.to_dict(),
    }
    write_json(output, similarity_report)
    return similarity_report


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


def _handle_analyze_transition(args, repo_root: Path) -> int:
    source_a = _resolve_path_argument(args.source_a, repo_root)
    source_b = _resolve_path_argument(args.source_b, repo_root)
    hint_output = _resolve_path_argument(args.hint_output, repo_root)
    analysis_output = _resolve_analysis_output(args.analysis_output, hint_output)
    comparison_output = (
        _resolve_path_argument(args.comparison_output, repo_root) if args.comparison_output else None
    )
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

    print(
        json.dumps(
            {
                "hint_output": str(hint_output),
                "analysis_output": str(analysis_output),
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


def _resolve_analysis_output(raw_path: str | None, hint_output: Path) -> Path:
    if raw_path:
        return Path(raw_path).resolve() if Path(raw_path).is_absolute() else Path(raw_path)

    if hint_output.suffix:
        base_name = hint_output.name[: -len(hint_output.suffix)]
    else:
        base_name = hint_output.name
    return hint_output.with_name(f"{base_name}.analysis.json")


def _format_path_for_output(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


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
        )

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
        "issues": [
            {"field": issue.field, "level": issue.level, "message": issue.message}
            for issue in validation.issues
        ],
    }
    embedded_plan = extract_plan_from_analysis(analysis_data) if analysis_data else None
    if embedded_plan is not None:
        result["embedded_plan"] = embedded_plan
        result["embedded_plan_summary"] = _summarize_plan_fields(embedded_plan)
    if recomputed_plan is not None:
        result["recomputed_plan"] = recomputed_plan
        result["recomputed_plan_summary"] = _summarize_plan_fields(recomputed_plan)
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


def _build_plan_comparison_report(
    analysis_file: str | None,
    job_output: Path | None,
    plan_source: str,
    selected_plan: dict[str, str | bool | None],
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
        "embedded_plan": embedded_plan,
        "embedded_plan_summary": embedded_plan_summary,
        "recomputed_plan": recomputed_plan,
        "recomputed_plan_summary": recomputed_plan_summary,
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

    summary = {
        "status": "succeeded" if overall_exit_code == 0 else "failed",
        "suite": suite_name,
        "renderer": renderer,
        "results": results,
    }
    summary_path = smoke_test_root / "smoke_test_report.json"
    write_json(summary_path, summary)

    print(json.dumps({"smoke_test_report": str(summary_path), "results": results}, indent=2))
    return overall_exit_code


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
