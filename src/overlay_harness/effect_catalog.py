from __future__ import annotations

from pathlib import Path
import json
import hashlib
from typing import Any


EFFECT_CATALOG_VERSION = 1
DEFAULT_EFFECT_CATALOG_RELATIVE_PATH = Path("harness/configs/effect_catalog.json")
DEFAULT_EFFECT_CATALOG_SOURCE_RELATIVE_PATH = Path("harness/configs/effect_catalog_sources.json")


_EFFECT_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "effect_id": "builtin-seamless",
        "mode": "builtin-seamless",
        "effect_source": "builtin",
        "family": "seamless",
        "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "style_hints": ("seamless", "smooth", "generated-seamless"),
        "retrieval_priority": 0,
        "source_documents": (
            "harness/examples/effect_specs/builtin_seamless_sliding.json",
            "harness/examples/render_job.sample.json",
            "harness/examples/render_job.sample.real.json",
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrSeamlessSliding.cpp",
        ),
    },
    {
        "effect_id": "builtin-glitch",
        "mode": "builtin-glitch",
        "effect_source": "builtin",
        "family": "glitch",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "style_hints": ("glitch", "generated-glitch"),
        "retrieval_priority": 0,
        "source_documents": (
            "harness/examples/render_job.effect_spec.sample.json",
            "harness/examples/render_job.effect_spec.sample.real.json",
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrGlitch.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrGlitchInfoManager.cpp",
        ),
    },
    {
        "effect_id": "builtin-camcorder",
        "mode": "builtin-camcorder",
        "effect_source": "builtin",
        "family": "camera",
        "fx_id": "CES_PlugIn_Camera.dll\\DSP_TR_Camera_Camcorder",
        "style_hints": ("camera", "camcorder"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrCamcorder.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrCamcorder_ps.hlsl",
            "overlaytrengine/OverlayTrPlugInFx/TrCamcorder_Blur_ps.hlsl",
            "overlaytrengine/OverlayTrPlugInFx/TrCamcorder_Overlay_ps.hlsl",
        ),
    },
    {
        "effect_id": "builtin-particle-spray",
        "mode": "builtin-particle-spray",
        "effect_source": "builtin",
        "family": "particle",
        "fx_id": "CES_PlugIn_ParticleSpray.dll\\DSP_TR_Sparkle_01",
        "style_hints": ("particle", "sparkle", "spray"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrParticleSpray.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrParticleSprayInfoManager.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrParticleSpray_ps.hlsl",
            "overlaytrengine/OverlayTrPlugInFx/TrParticleSpray_Particle_ps.hlsl",
            "overlaytrengine/OverlayTrPlugInFx/TrParticleSpray_Particle_vs.hlsl",
        ),
    },
    {
        "effect_id": "builtin-frame-overlay",
        "mode": "builtin-frame-overlay",
        "effect_source": "builtin",
        "family": "overlay",
        "fx_id": "CES_PlugIn_FrameOverlay.dll\\DSP_TR_FrameOverlay_01",
        "style_hints": ("frame-overlay", "film-roll", "overlay"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrFrameOverlay.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrFrameOverlay_ps.hlsl",
            "overlaytrengine/OverlayTrPlugInFx/TrFrameOverlay_Noise_ps.hlsl",
        ),
    },
    {
        "effect_id": "builtin-blur",
        "mode": "builtin-blur",
        "effect_source": "builtin",
        "family": "blur",
        "fx_id": "Blur_DollarBokeh",
        "style_hints": ("blur", "bokeh"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp",
        ),
    },
    {
        "effect_id": "builtin-ui-snapshot",
        "mode": "builtin-ui-snapshot",
        "effect_source": "builtin",
        "family": "ui",
        "fx_id": "UI_Snapshot",
        "style_hints": ("ui", "snapshot"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp",
        ),
    },
    {
        "effect_id": "builtin-glitch-distortion",
        "mode": "builtin-glitch-distortion",
        "effect_source": "builtin",
        "family": "glitch",
        "fx_id": "Glitch_HDistor1",
        "style_hints": ("distortion", "glitch2"),
        "retrieval_priority": 0,
        "source_documents": (
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h",
            "overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp",
        ),
    },
    {
        "effect_id": "generated-seamless-placeholder",
        "mode": "generated-seamless-placeholder",
        "effect_source": "generated",
        "family": "seamless",
        "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "fallback_fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
        "style_hints": ("generated-seamless",),
        "retrieval_priority": 10,
        "source_documents": (
            "harness/examples/effect_specs/generated_SeamlessSliding_placeholder.json",
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrSeamlessSliding.cpp",
        ),
    },
    {
        "effect_id": "generated-glitch-placeholder",
        "mode": "generated-glitch-placeholder",
        "effect_source": "generated",
        "family": "glitch",
        "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "fallback_fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
        "style_hints": ("generated-glitch",),
        "retrieval_priority": 10,
        "source_documents": (
            "harness/examples/effect_specs/generated_glitch_placeholder.json",
            "overlaytrengine/OverlayTrPlugInFx/FxInfo.h",
            "overlaytrengine/OverlayTrPlugInFx/OverlayTrPlugInFx.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrGlitch.cpp",
            "overlaytrengine/OverlayTrPlugInFx/TrGlitchInfoManager.cpp",
        ),
    },
)


def build_effect_catalog(
    repo_root: Path,
    source_manifest_path: Path | None = None,
) -> dict[str, Any]:
    source_manifest = _load_effect_catalog_source_manifest(repo_root, source_manifest_path)
    source_blueprints = (
        source_manifest["registrations"]
        if source_manifest is not None
        else list(_EFFECT_BLUEPRINTS)
    )
    effects = [_build_effect_record(repo_root, blueprint) for blueprint in source_blueprints]
    retrieval_index = {
        style: record["effect_id"]
        for record in effects
        for style in record["style_hints"]
        if record["effect_source"] == "builtin"
    }
    source_manifest_relative = None
    source_manifest_version = None
    source_manifest_sha256 = None
    if source_manifest is not None:
        source_manifest_relative = _format_optional_repo_path(
            source_manifest["source_manifest_path"],
            repo_root,
        )
        source_manifest_version = source_manifest["catalog_version"]
        source_manifest_sha256 = _hash_file(source_manifest["source_manifest_path"])
    return {
        "catalog_type": "effect_catalog",
        "catalog_version": EFFECT_CATALOG_VERSION,
        "source_root": "harness",
        "source_manifest": source_manifest_relative,
        "source_manifest_version": source_manifest_version,
        "source_manifest_sha256": source_manifest_sha256,
        "registration_count": len(effects),
        "effects": effects,
        "retrieval_index": retrieval_index,
    }


def build_effect_catalog_audit(
    repo_root: Path,
    source_manifest_path: Path | None = None,
) -> dict[str, Any]:
    source_manifest = _load_effect_catalog_source_manifest(repo_root, source_manifest_path)
    manifest_registrations = source_manifest["registrations"] if source_manifest is not None else []

    baseline_effect_ids = {blueprint["effect_id"] for blueprint in _EFFECT_BLUEPRINTS}
    manifest_effect_ids = {
        registration["effect_id"]
        for registration in manifest_registrations
        if isinstance(registration, dict) and isinstance(registration.get("effect_id"), str)
    }

    missing_effect_ids = sorted(baseline_effect_ids - manifest_effect_ids)
    extra_effect_ids = sorted(manifest_effect_ids - baseline_effect_ids)

    audit_status = "ok"
    if source_manifest is None:
        audit_status = "missing_source_manifest"
    elif missing_effect_ids or extra_effect_ids:
        audit_status = "mismatch"

    return {
        "report_type": "effect_catalog_audit",
        "report_version": EFFECT_CATALOG_VERSION,
        "status": audit_status,
        "source_manifest": _format_optional_repo_path(
            source_manifest["source_manifest_path"],
            repo_root,
        )
        if source_manifest is not None
        else None,
        "source_manifest_version": source_manifest["catalog_version"] if source_manifest is not None else None,
        "source_manifest_sha256": _hash_file(source_manifest["source_manifest_path"])
        if source_manifest is not None
        else None,
        "baseline_registration_count": len(_EFFECT_BLUEPRINTS),
        "manifest_registration_count": len(manifest_registrations),
        "missing_effect_ids": missing_effect_ids,
        "extra_effect_ids": extra_effect_ids,
    }


def load_effect_catalog(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    if catalog.get("catalog_type") != "effect_catalog":
        raise ValueError(f"effect catalog is invalid: {file_path}")
    return catalog


def select_effect_candidate(catalog: dict[str, Any], style: str, input_kind: str) -> dict[str, Any] | None:
    effects = catalog.get("effects")
    if not isinstance(effects, list):
        return None

    candidates = [
        effect
        for effect in effects
        if isinstance(effect, dict)
        and effect.get("effect_source") == "builtin"
        and style in set(effect.get("style_hints", []))
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda effect: (
            int(effect.get("retrieval_priority", 999)),
            str(effect.get("family", "")),
            str(effect.get("effect_id", "")),
        )
    )
    selected = candidates[0]
    return {
        "effect_id": selected.get("effect_id"),
        "mode": selected.get("mode"),
        "fx_id": selected.get("fx_id"),
        "family": selected.get("family"),
        "style": style,
        "input_kind": input_kind,
        "retrieval_source": "effect_catalog",
        "retrieval_priority": selected.get("retrieval_priority"),
        "source_documents": selected.get("source_documents"),
    }


def _load_effect_catalog_source_manifest(
    repo_root: Path,
    source_manifest_path: Path | None = None,
) -> dict[str, Any] | None:
    if source_manifest_path is None:
        source_manifest_path = repo_root / DEFAULT_EFFECT_CATALOG_SOURCE_RELATIVE_PATH
    elif not source_manifest_path.is_absolute():
        source_manifest_path = (repo_root / source_manifest_path).resolve()

    if not source_manifest_path.exists():
        return None

    with source_manifest_path.open("r", encoding="utf-8") as handle:
        source_manifest = json.load(handle)

    if source_manifest.get("catalog_type") != "effect_catalog_sources":
        raise ValueError(f"effect catalog source manifest is invalid: {source_manifest_path}")

    registrations = source_manifest.get("registrations")
    if not isinstance(registrations, list):
        raise ValueError("effect catalog source manifest registrations must be a list")

    seen_effect_ids: set[str] = set()
    for registration in registrations:
        if not isinstance(registration, dict):
            raise ValueError("effect catalog source manifest registrations must contain objects")
        effect_id = registration.get("effect_id")
        if not isinstance(effect_id, str) or not effect_id:
            raise ValueError("effect catalog source manifest registrations must include a non-empty effect_id")
        if effect_id in seen_effect_ids:
            raise ValueError(f"duplicate effect_id in effect catalog source manifest: {effect_id}")
        seen_effect_ids.add(effect_id)
        mode = registration.get("mode")
        effect_source = registration.get("effect_source")
        family = registration.get("family")
        fx_id = registration.get("fx_id")
        if not isinstance(mode, str) or not mode:
            raise ValueError(f"effect catalog source manifest registration '{effect_id}' must include a non-empty mode")
        if effect_source not in {"builtin", "generated"}:
            raise ValueError(
                f"effect catalog source manifest registration '{effect_id}' must use effect_source builtin or generated"
            )
        if not isinstance(family, str) or not family:
            raise ValueError(f"effect catalog source manifest registration '{effect_id}' must include a non-empty family")
        if not isinstance(fx_id, str) or not fx_id:
            raise ValueError(f"effect catalog source manifest registration '{effect_id}' must include a non-empty fx_id")
        if effect_source == "generated" and not isinstance(registration.get("fallback_fx_id"), str):
            raise ValueError(
                f"effect catalog source manifest registration '{effect_id}' must include fallback_fx_id for generated entries"
            )
        if effect_source == "builtin" and "fallback_fx_id" in registration:
            raise ValueError(
                f"effect catalog source manifest registration '{effect_id}' must not include fallback_fx_id for builtin entries"
            )
        style_hints = registration.get("style_hints")
        if not isinstance(style_hints, list) or not all(isinstance(style, str) and style for style in style_hints):
            raise ValueError(f"effect catalog source manifest registration '{effect_id}' must include string style_hints")
        source_documents = registration.get("source_documents")
        if not isinstance(source_documents, list) or not source_documents or not all(
            isinstance(source_document, str) and source_document for source_document in source_documents
        ):
            raise ValueError(
                f"effect catalog source manifest registration '{effect_id}' must include non-empty string source_documents"
            )
        for source_document in source_documents:
            source_document_path = (repo_root / source_document).resolve()
            if not source_document_path.exists():
                raise ValueError(
                    f"effect catalog source manifest registration '{effect_id}' references missing source document: "
                    f"{source_document}"
                )

    return {
        "catalog_type": source_manifest["catalog_type"],
        "catalog_version": source_manifest.get("catalog_version", 1),
        "source_manifest_path": source_manifest_path,
        "registrations": registrations,
    }


def _build_effect_record(repo_root: Path, blueprint: dict[str, Any]) -> dict[str, Any]:
    record = dict(blueprint)
    record["style_hints"] = list(blueprint["style_hints"])
    record["source_documents"] = [
        _format_repo_path((repo_root / relative_path).resolve(), repo_root)
        for relative_path in blueprint["source_documents"]
    ]
    return record


def _format_repo_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


def _format_optional_repo_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    return _format_repo_path(path, repo_root)


def _hash_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
