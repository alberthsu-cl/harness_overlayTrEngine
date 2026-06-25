from __future__ import annotations

from pathlib import Path

from .models import RenderJob, ValidationIssue, ValidationResult


def validate_job(job: RenderJob, repo_root: Path, allowed_effects: dict) -> ValidationResult:
    result = ValidationResult()

    if not job.job_name.strip():
        result.issues.append(ValidationIssue("job_name", "job_name must not be empty"))

    if job.effect.category not in set(allowed_effects.get("allowed_categories", [])):
        result.issues.append(
            ValidationIssue(
                "effect.category",
                f"effect category '{job.effect.category}' is not allowed by current policy",
            )
        )

    for required_uniform in allowed_effects.get("required_uniforms", []):
        if required_uniform not in job.effect.uniforms:
            result.issues.append(
                ValidationIssue(
                    "effect.uniforms",
                    f"missing required uniform '{required_uniform}'",
                )
            )

    if job.render.width % 2 != 0 or job.render.height % 2 != 0:
        result.issues.append(
            ValidationIssue("render", "width and height should be even numbers", level="warning")
        )

    if job.render.output_format != "png_sequence":
        result.issues.append(
            ValidationIssue(
                "render.output_format",
                "only png_sequence is supported in the initial scaffold",
            )
        )

    for field_name, raw_path in {
        "inputs.source_a": job.inputs.source_a,
        "inputs.source_b": job.inputs.source_b,
    }.items():
        resolved = _resolve_repo_path(repo_root, raw_path)
        if not resolved.exists():
            result.issues.append(
                ValidationIssue(field_name, f"path does not exist: {resolved}")
            )

    if job.inputs.reference_transition:
        resolved_reference = _resolve_repo_path(repo_root, job.inputs.reference_transition)
        if not resolved_reference.exists():
            result.issues.append(
                ValidationIssue(
                    "inputs.reference_transition",
                    f"path does not exist: {resolved_reference}",
                )
            )

    if job.effect.effect_spec:
        effect_spec_path = _resolve_repo_path(repo_root, job.effect.effect_spec)
        if not effect_spec_path.exists():
            result.issues.append(
                ValidationIssue(
                    "effect.effect_spec",
                    f"path does not exist: {effect_spec_path}",
                )
            )

    return result


def _resolve_repo_path(repo_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (repo_root / candidate).resolve()