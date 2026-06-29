from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from .config import load_allowed_effects, load_eval_thresholds
from .models import load_render_job
from .planner import (
    auto_input_kinds,
    auto_styles,
    build_planned_job,
    load_transition_hint,
    planner_modes,
    planner_preset,
    planner_presets,
    resolve_auto_plan,
)
from .renderer import prepare_render_invocation
from .report import HarnessReport
from .validator import validate_job
from .video_prep import extract_video_frames, prepare_solid_color_frames
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
    if args.command == "plan-job":
        return _handle_plan_job(args, repo_root, config_dir)
    if args.command == "smoke-test":
        return _handle_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)
    if args.command == "real-smoke-test":
        return _handle_real_smoke_test(args, repo_root, harness_root, config_dir, default_renderer)

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
    prepare_video.add_argument("--frame-count", type=int, default=30, help="Frame count for solid-color generation")
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
    plan_job.add_argument("--frame-count", type=int, default=30, help="Target render frame count")
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
                frame_count=args.frame_count,
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


def _handle_plan_job(args, repo_root: Path, config_dir: Path) -> int:
    hint_data: dict | None = None
    if args.hint_file:
        hint_path = _resolve_path_argument(args.hint_file, repo_root)
        try:
            hint_data = load_transition_hint(hint_path)
        except Exception as exc:
            print(f"plan-job failed: could not load hint file: {exc}")
            return 1

    hint_preset = hint_data.get("preset") if hint_data else None
    hint_style = hint_data.get("style_hint") if hint_data else None
    hint_input_kind = hint_data.get("input_kind") if hint_data else None
    hint_reference_transition = hint_data.get("reference_transition") if hint_data else None
    hint_job_name = hint_data.get("job_name") if hint_data else None

    preset_name = args.preset
    if not preset_name and hint_preset:
        preset_name = hint_preset
    preset = planner_preset(preset_name) if preset_name else {}

    source_a_for_auto = None
    source_b_for_auto = None
    auto_input_kind = None
    auto_mode = None

    auto_requested = args.auto or bool(hint_style)

    if auto_requested:
        effective_style = args.style or hint_style
        effective_input_kind = hint_input_kind or args.input_kind

        if not effective_style:
            print("plan-job failed: --style is required when --auto is used")
            return 1
        if not args.source_a or not args.source_b:
            print("plan-job failed: --source-a and --source-b are required when --auto is used")
            return 1

        source_a_for_auto = _resolve_path_argument(str(args.source_a), repo_root)
        source_b_for_auto = _resolve_path_argument(str(args.source_b), repo_root)
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

    source_a_raw = args.source_a or preset.get("source_a")
    source_b_raw = args.source_b or preset.get("source_b")
    job_output_raw = args.job_output or preset.get("job_output")
    mode = args.mode or auto_mode or preset.get("mode")
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
        else _resolve_path_argument(str(hint_reference_transition), repo_root)
        if hint_reference_transition
        else None
    )

    try:
        job, effect_spec_payload = build_planned_job(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            mode=str(mode),
            width=args.width,
            height=args.height,
            fps=args.fps,
            frame_count=args.frame_count,
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
        "style": args.style or hint_style,
        "input_kind": auto_input_kind or hint_input_kind or args.input_kind,
        "hint_file": args.hint_file,
        "job_name": job.job_name,
        "validation_valid": validation.is_valid,
        "issues": [
            {"field": issue.field, "level": issue.level, "message": issue.message}
            for issue in validation.issues
        ],
    }
    if effect_spec_output is not None and effect_spec_payload is not None:
        result["effect_spec_output"] = str(effect_spec_output)

    print(json.dumps(result, indent=2))
    return 0 if validation.is_valid else 1


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