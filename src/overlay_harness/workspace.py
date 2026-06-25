from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
import json

from .models import RenderJob


@dataclass(slots=True)
class JobWorkspace:
    root: Path
    inputs_dir: Path
    render_dir: Path
    reports_dir: Path
    artifacts_dir: Path


def create_job_workspace(harness_root: Path, job: RenderJob) -> JobWorkspace:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    job_root = harness_root / "work" / f"{job.job_name}_{timestamp}"
    inputs_dir = job_root / "inputs"
    render_dir = job_root / "render"
    reports_dir = job_root / "reports"
    artifacts_dir = job_root / "artifacts"

    for path in (inputs_dir, render_dir, reports_dir, artifacts_dir):
        path.mkdir(parents=True, exist_ok=False)

    return JobWorkspace(
        root=job_root,
        inputs_dir=inputs_dir,
        render_dir=render_dir,
        reports_dir=reports_dir,
        artifacts_dir=artifacts_dir,
    )


def write_json(file_path: Path, payload: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")