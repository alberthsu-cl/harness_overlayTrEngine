from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from .config import load_allowed_effects, load_eval_thresholds
from .models import load_render_job
from .renderer import prepare_render_invocation
from .report import HarnessReport
from .validator import validate_job
from .video_prep import extract_video_frames, prepare_solid_color_frames
from .workspace import create_job_workspace, write_json


OFFICIAL_SMOKE_TEST_JOBS = (
    "harness/examples/render_job.sample.json",
    "harness/examples/render_job.effect_spec.sample.json",
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    harness_root = repo_root / "harness"
    config_dir = harness_root / "configs"

    if args.command == "prepare-video":
        return _handle_prepare_video(args, repo_root)
    if args.command == "prepare-pair":
        return _handle_prepare_pair(args, repo_root)
    if args.command == "smoke-test":
        return _handle_smoke_test(args, repo_root, harness_root, config_dir)

    result = _execute_job_command(
        repo_root=repo_root,
        harness_root=harness_root,
        config_dir=config_dir,
        job_path=Path(args.job).resolve(),
        command_name=args.command,
        renderer=getattr(args, "renderer", None),
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
                help="Path to the future headless renderer executable",
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

    smoke_test = subparsers.add_parser(
        "smoke-test",
        help="Run the two official current-phase smoke-test jobs",
    )
    smoke_test.add_argument(
        "--renderer",
        required=False,
        help="Optional path to the native renderer executable for full render smoke tests",
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


def _handle_smoke_test(args, repo_root: Path, harness_root: Path, config_dir: Path) -> int:
    smoke_test_root = harness_root / "work" / f"smoke_test_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    smoke_test_root.mkdir(parents=True, exist_ok=False)

    results: list[dict] = []
    overall_exit_code = 0

    for relative_job_path in OFFICIAL_SMOKE_TEST_JOBS:
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

        if args.renderer:
            run_result = _execute_job_command(
                repo_root=repo_root,
                harness_root=harness_root,
                config_dir=config_dir,
                job_path=job_path,
                command_name="run",
                renderer=args.renderer,
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
        "renderer": args.renderer,
        "results": results,
    }
    summary_path = smoke_test_root / "smoke_test_report.json"
    write_json(summary_path, summary)

    print(json.dumps({"smoke_test_report": str(summary_path), "results": results}, indent=2))
    return overall_exit_code


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