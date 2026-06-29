from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from .models import RenderJob
from .workspace import JobWorkspace, write_json


@dataclass(slots=True)
class RenderInvocation:
    renderer_executable: str | None
    request_file: Path
    expected_output_dir: Path
    result_file: Path
    status: str
    message: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    produced_frame_count: int = 0
    expected_frame_count: int = 0
    output_check_message: str = ""
    renderer_result: dict | None = None


def prepare_render_invocation(
    repo_root: Path,
    workspace: JobWorkspace,
    job: RenderJob,
    renderer_executable: str | None,
) -> RenderInvocation:
    request_file = workspace.render_dir / "render_request.json"
    result_file = workspace.render_dir / "renderer_result.json"
    payload = {
        "job": job.to_dict(),
        "repo_root": str(repo_root),
        "output_dir": str(workspace.artifacts_dir),
        "notes": [
            "This request file is the contract between the Python harness and the headless C++ renderer shim.",
            "Rendering works when a built native renderer executable is provided via --renderer.",
        ],
    }
    write_json(request_file, payload)

    if renderer_executable:
        renderer_path = Path(renderer_executable)
        if renderer_path.exists():
            completed = subprocess.run(
                [str(renderer_path), "--request", str(request_file)],
                capture_output=True,
                text=True,
                check=False,
            )

            status = "succeeded" if completed.returncode == 0 else "failed"
            message = (
                "renderer completed successfully"
                if completed.returncode == 0
                else f"renderer exited with code {completed.returncode}"
            )

            produced_frame_count, output_check_message = inspect_render_outputs(
                workspace.artifacts_dir,
                job.render.frame_count,
            )
            renderer_result = load_renderer_result(result_file)

            if completed.returncode == 0 and produced_frame_count != job.render.frame_count:
                status = "failed"
                message = (
                    f"renderer exited successfully but produced {produced_frame_count} "
                    f"of {job.render.frame_count} expected frames"
                )
            elif output_check_message and completed.returncode == 0:
                message = output_check_message

            return RenderInvocation(
                renderer_executable=renderer_executable,
                request_file=request_file,
                expected_output_dir=workspace.artifacts_dir,
                result_file=result_file,
                status=status,
                message=message,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                produced_frame_count=produced_frame_count,
                expected_frame_count=job.render.frame_count,
                output_check_message=output_check_message,
                renderer_result=renderer_result,
            )

    return RenderInvocation(
        renderer_executable=renderer_executable,
        request_file=request_file,
        expected_output_dir=workspace.artifacts_dir,
        result_file=result_file,
        status="blocked",
        message="renderer executable is not available yet; render request recorded only",
        expected_frame_count=job.render.frame_count,
    )


def inspect_render_outputs(artifacts_dir: Path, expected_frame_count: int) -> tuple[int, str]:
    frame_files = sorted(artifacts_dir.glob("frame_*.png"))
    produced_frame_count = len(frame_files)

    if produced_frame_count == 0:
        return 0, "renderer produced no PNG frames"
    if produced_frame_count != expected_frame_count:
        return produced_frame_count, (
            f"renderer produced {produced_frame_count} PNG frames; "
            f"expected {expected_frame_count}"
        )

    return produced_frame_count, f"renderer produced {produced_frame_count} expected PNG frames"


def load_renderer_result(result_file: Path) -> dict | None:
    if not result_file.exists():
        return None

    with result_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)