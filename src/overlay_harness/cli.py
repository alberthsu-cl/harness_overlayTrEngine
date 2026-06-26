from __future__ import annotations

import argparse
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

    job = load_render_job(Path(args.job).resolve())
    allowed_effects = load_allowed_effects(config_dir)
    validation = validate_job(job, repo_root, allowed_effects)

    if args.command == "validate":
        return _handle_validate(validation)

    if not validation.is_valid:
        _print_validation(validation)
        return 1

    workspace = create_job_workspace(harness_root, job)
    write_json(workspace.inputs_dir / "job.normalized.json", job.to_dict())
    write_json(workspace.inputs_dir / "allowed_effects.json", allowed_effects)
    write_json(workspace.inputs_dir / "eval_thresholds.json", load_eval_thresholds(config_dir))

    if args.command == "prepare":
        print(f"Prepared workspace: {workspace.root}")
        return 0

    invocation = prepare_render_invocation(repo_root, workspace, job, args.renderer)
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

    print(json.dumps({"workspace": str(workspace.root), "report": str(report_path)}, indent=2))
    return 0


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

    return parser


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