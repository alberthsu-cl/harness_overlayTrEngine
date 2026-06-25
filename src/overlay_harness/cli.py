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
from .workspace import create_job_workspace, write_json


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    harness_root = repo_root / "harness"
    config_dir = harness_root / "configs"

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
            "expected_output_dir": str(invocation.expected_output_dir),
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

    return parser


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