from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import RenderJob
from .workspace import JobWorkspace, write_json


@dataclass(slots=True)
class RenderInvocation:
    renderer_executable: str | None
    request_file: Path
    expected_output_dir: Path
    status: str
    message: str


def prepare_render_invocation(
    repo_root: Path,
    workspace: JobWorkspace,
    job: RenderJob,
    renderer_executable: str | None,
) -> RenderInvocation:
    request_file = workspace.render_dir / "render_request.json"
    payload = {
        "job": job.to_dict(),
        "repo_root": str(repo_root),
        "output_dir": str(workspace.artifacts_dir),
        "notes": [
            "This request file is the contract for the future headless C++ renderer shim.",
            "The current Python scaffold does not render frames yet.",
        ],
    }
    write_json(request_file, payload)

    if renderer_executable:
        renderer_path = Path(renderer_executable)
        if renderer_path.exists():
            return RenderInvocation(
                renderer_executable=renderer_executable,
                request_file=request_file,
                expected_output_dir=workspace.artifacts_dir,
                status="ready",
                message="renderer executable exists; CLI integration can be wired next",
            )

    return RenderInvocation(
        renderer_executable=renderer_executable,
        request_file=request_file,
        expected_output_dir=workspace.artifacts_dir,
        status="blocked",
        message="renderer executable is not available yet; render request recorded only",
    )