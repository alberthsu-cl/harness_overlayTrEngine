from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass(slots=True)
class EffectSpec:
    fx_id: str
    category: str
    effect_spec: str | None = None
    uniforms: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InputSpec:
    source_a: str
    source_b: str
    reference_transition: str | None = None


@dataclass(slots=True)
class RenderSettings:
    width: int
    height: int
    fps: int
    frame_count: int
    output_format: str


@dataclass(slots=True)
class RenderJob:
    job_name: str
    effect: EffectSpec
    inputs: InputSpec
    render: RenderSettings

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RenderJob":
        return cls(
            job_name=data["job_name"],
            effect=EffectSpec(**data["effect"]),
            inputs=InputSpec(**data["inputs"]),
            render=RenderSettings(**data["render"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ValidationIssue:
    field: str
    message: str
    level: str = "error"


@dataclass(slots=True)
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)


def load_json(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_render_job(file_path: Path) -> RenderJob:
    return RenderJob.from_dict(load_json(file_path))