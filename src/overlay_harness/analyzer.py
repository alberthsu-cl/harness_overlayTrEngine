from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typing import Any, Protocol

from .planner import GENERATED_EFFECT_SUPPORTED_STYLES, auto_styles, build_recommended_plan, infer_input_kind


STYLE_HINTS = set(auto_styles())
ANALYSIS_ARTIFACT_VERSION = 2
ANALYSIS_ENGINE = "deterministic_rules_v1"
ANALYSIS_PROVIDER_KIND = "deterministic_rules"
ANALYSIS_PROVIDER_NAME = ANALYSIS_ENGINE
MODEL_EXECUTION_CONTRACT_TYPE = "transition_analysis_model_execution"
MODEL_EXECUTION_CONTRACT_VERSION = 1


class TransitionAnalysisProvider(Protocol):
    def analyze_transition(
        self,
        *,
        repo_root: Path,
        source_a: Path,
        source_b: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a transition hint that matches the current analyzer contract."""

    def analyze_transition_video(
        self,
        *,
        repo_root: Path,
        transition_video: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        transition_window: dict[str, Any] | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a transition hint derived from a sample transition video."""


class TransitionModelExecutor(Protocol):
    def execute_model_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        """Execute a model-backed analysis request and return the normalized result contract."""


class DeterministicTransitionAnalysisProvider:
    def analyze_transition(
        self,
        *,
        repo_root: Path,
        source_a: Path,
        source_b: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        detected_input_kind = input_kind
        if detected_input_kind == "auto":
            detected_input_kind = infer_input_kind(repo_root, source_a, source_b)

        source_a_signals = inspect_prepared_input(source_a)
        source_b_signals = inspect_prepared_input(source_b)
        pair_signals = summarize_pair_signals(source_a_signals, source_b_signals)

        resolved_style_hint, reason = _resolve_style_hint(
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            detected_input_kind=detected_input_kind,
            pair_signals=pair_signals,
        )

        notes = f"Analyzer selected '{resolved_style_hint}' because {reason}."
        if intent:
            notes += f" Intent: {intent}"

        return {
            "analysis_provider": _build_transition_analysis_provider(
                provider_kind=ANALYSIS_PROVIDER_KIND,
                provider_name=ANALYSIS_PROVIDER_NAME,
                provider_mode="deterministic",
            ),
            "style_hint": resolved_style_hint,
            "input_kind": detected_input_kind,
            "reference_transition": _format_optional_path(reference_transition, repo_root),
            "job_name": job_name,
            "notes": notes,
            "analysis": {
                "intent": intent,
                "prefer_generated": prefer_generated,
                "style_reason": reason,
                "signals": pair_signals,
            },
        }

    def analyze_transition_video(
        self,
        *,
        repo_root: Path,
        transition_video: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        transition_window: dict[str, Any] | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_input_kind = input_kind
        if resolved_input_kind == "auto":
            resolved_input_kind = analyzer_inputs.get("input_kind") if isinstance(analyzer_inputs, dict) else "auto"
            if not isinstance(resolved_input_kind, str) or not resolved_input_kind:
                resolved_input_kind = "auto"

        resolved_style_hint, reason = _resolve_video_style_hint(
            transition_video=transition_video,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            transition_window=transition_window,
        )

        notes = f"Video analyzer selected '{resolved_style_hint}' because {reason}."
        if intent:
            notes += f" Intent: {intent}"

        return {
            "analysis_provider": _build_transition_analysis_provider(
                provider_kind=ANALYSIS_PROVIDER_KIND,
                provider_name=ANALYSIS_PROVIDER_NAME,
                provider_mode="video_analysis",
            ),
            "analysis_source": "transition_video",
            "transition_video": _format_optional_path(transition_video, repo_root),
            "transition_window": _build_transition_window_analysis(transition_window),
            "style_hint": resolved_style_hint,
            "input_kind": resolved_input_kind,
            "reference_transition": _format_optional_path(reference_transition, repo_root),
            "job_name": job_name,
            "notes": notes,
            "analysis": {
                "intent": intent,
                "prefer_generated": prefer_generated,
                "style_reason": reason,
                "signals": {
                    "transition_video": _format_optional_path(transition_video, repo_root),
                    "transition_window": _build_transition_window_analysis(transition_window),
                },
            },
        }


class DeterministicTransitionModelExecutor:
    def execute_model_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        deterministic_provider = DeterministicTransitionAnalysisProvider()
        inputs = model_request["inputs"]
        transition_window = inputs.get("transition_window") if isinstance(inputs, dict) else None
        analysis_source = inputs.get("analysis_source") if isinstance(inputs, dict) else None
        if analysis_source == "transition_video":
            hint = deterministic_provider.analyze_transition_video(
                repo_root=Path(inputs["repo_root"]),
                transition_video=Path(inputs["transition_video"]),
                input_kind=inputs["input_kind"],
                style_hint=inputs["style_hint"],
                intent=inputs["intent"],
                prefer_generated=bool(inputs["prefer_generated"]),
                reference_transition=Path(inputs["reference_transition"])
                if inputs["reference_transition"]
                else None,
                job_name=inputs["job_name"],
                transition_window=transition_window if isinstance(transition_window, dict) else None,
                analyzer_inputs=model_request["analyzer_inputs"],
            )
        else:
            hint = deterministic_provider.analyze_transition(
                repo_root=Path(inputs["repo_root"]),
                source_a=Path(inputs["source_a"]),
                source_b=Path(inputs["source_b"]),
                input_kind=inputs["input_kind"],
                style_hint=inputs["style_hint"],
                intent=inputs["intent"],
                prefer_generated=bool(inputs["prefer_generated"]),
                reference_transition=Path(inputs["reference_transition"])
                if inputs["reference_transition"]
                else None,
                job_name=inputs["job_name"],
                analyzer_inputs=model_request["analyzer_inputs"],
            )
        return {
            "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
            "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
            "status": "delegated_to_deterministic_fallback",
            "execution_mode": "pending_model_execution",
            "notes": "model execution boundary is stubbed and delegates to deterministic analysis",
            "hint": hint,
        }


class ModelBackedTransitionAnalysisProvider:
    def __init__(
        self,
        *,
        resolved_name: str,
        model_executor: TransitionModelExecutor | None = None,
    ) -> None:
        self._resolved_name = resolved_name
        self._model_executor = model_executor or DeterministicTransitionModelExecutor()

    def analyze_transition(
        self,
        *,
        repo_root: Path,
        source_a: Path,
        source_b: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        model_request = self.build_model_execution_request(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            transition_video=None,
            analysis_source="source_a_source_b",
            input_kind=input_kind,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            reference_transition=reference_transition,
            job_name=job_name,
            transition_window=None,
            analyzer_inputs=analyzer_inputs,
        )
        model_result = self.invoke_model_execution(model_request=model_request)
        hint = model_result["hint"]
        hint.setdefault("analysis", {})
        hint["analysis"]["model_execution"] = {
            "request": model_request,
            "status": model_result["status"],
            "execution_mode": model_result["execution_mode"],
        }
        hint["analysis"]["model_execution_request"] = model_request
        hint["analysis"]["model_execution_status"] = model_result["status"]
        hint["analysis"]["model_execution_mode"] = model_result["execution_mode"]
        hint["analysis"]["model_execution_notes"] = model_result["notes"]
        hint["analysis_provider"] = _build_transition_analysis_provider(
            provider_kind="model_backed",
            provider_name=self._resolved_name,
            provider_mode=model_result["execution_mode"],
        )
        return hint

    def analyze_transition_video(
        self,
        *,
        repo_root: Path,
        transition_video: Path,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        transition_window: dict[str, Any] | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        model_request = self.build_model_execution_request(
            repo_root=repo_root,
            source_a=None,
            source_b=None,
            transition_video=transition_video,
            analysis_source="transition_video",
            input_kind=input_kind,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            reference_transition=reference_transition,
            job_name=job_name,
            transition_window=transition_window,
            analyzer_inputs=analyzer_inputs,
        )
        model_result = self.invoke_model_execution(model_request=model_request)
        hint = model_result["hint"]
        hint.setdefault("analysis", {})
        hint["analysis"]["model_execution"] = {
            "request": model_request,
            "status": model_result["status"],
            "execution_mode": model_result["execution_mode"],
        }
        hint["analysis"]["model_execution_request"] = model_request
        hint["analysis"]["model_execution_status"] = model_result["status"]
        hint["analysis"]["model_execution_mode"] = model_result["execution_mode"]
        hint["analysis"]["model_execution_notes"] = model_result["notes"]
        hint["analysis_provider"] = _build_transition_analysis_provider(
            provider_kind="model_backed",
            provider_name=self._resolved_name,
            provider_mode=model_result["execution_mode"],
        )
        return hint

    def build_model_execution_request(
        self,
        *,
        repo_root: Path,
        source_a: Path | None,
        source_b: Path | None,
        transition_video: Path | None,
        analysis_source: str,
        input_kind: str,
        style_hint: str | None,
        intent: str | None,
        prefer_generated: bool,
        reference_transition: Path | None,
        job_name: str | None,
        transition_window: dict[str, Any] | None,
        analyzer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "repo_root": str(repo_root),
            "analysis_source": analysis_source,
            "input_kind": input_kind,
            "style_hint": style_hint,
            "intent": intent,
            "prefer_generated": prefer_generated,
            "reference_transition": _format_optional_path(reference_transition, repo_root),
            "job_name": job_name,
        }
        if source_a is not None:
            inputs["source_a"] = str(source_a)
        if source_b is not None:
            inputs["source_b"] = str(source_b)
        if transition_video is not None:
            inputs["transition_video"] = _format_optional_path(transition_video, repo_root)
        if transition_window is not None:
            inputs["transition_window"] = transition_window

        execution_contract = {
            "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
            "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
            "expected_status": "delegated_to_deterministic_fallback",
            "result_contract": {
                "style_hint": "str",
                "input_kind": "str",
                "reference_transition": "str | None",
                "job_name": "str | None",
                "notes": "str",
                "analysis": "dict[str, Any]",
            },
        }
        if analysis_source == "transition_video":
            execution_contract["input_contract"] = {
                "analysis_source": "transition_video",
                "transition_video": "Path",
                "transition_window": "dict[str, Any] | None",
            }
        else:
            execution_contract["input_contract"] = {
                "analysis_source": "source_a_source_b",
                "source_a": "Path",
                "source_b": "Path",
            }

        return {
            "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
            "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
            "provider": {
                "kind": "model_backed",
                "name": self._resolved_name,
                "mode": "vision",
            },
            "inputs": inputs,
            "analyzer_inputs": analyzer_inputs,
            "execution": execution_contract,
        }

    def invoke_model_execution(self, *, model_request: dict[str, Any]) -> dict[str, Any]:
        _validate_model_execution_request(model_request)
        model_result = self._model_executor.execute_model_request(model_request)
        _validate_model_execution_result(model_result)
        return model_result


def build_transition_model_executor() -> TransitionModelExecutor:
    return DeterministicTransitionModelExecutor()


def _validate_model_execution_request(model_request: dict[str, Any]) -> None:
    if not isinstance(model_request, dict):
        raise ValueError("model execution request must be a JSON object")
    provider = model_request.get("provider")
    inputs = model_request.get("inputs")
    analyzer_inputs = model_request.get("analyzer_inputs")
    execution = model_request.get("execution")
    if not isinstance(provider, dict) or not isinstance(inputs, dict) or not isinstance(analyzer_inputs, dict) or not isinstance(execution, dict):
        raise ValueError("model execution request is missing required sections")
    for field_name in ("kind", "name", "mode"):
        value = provider.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"model execution request provider.{field_name} must be a non-empty string")
    for field_name in ("repo_root", "analysis_source", "input_kind", "prefer_generated", "job_name"):
        if field_name not in inputs:
            raise ValueError(f"model execution request inputs.{field_name} is required")
    analysis_source = inputs.get("analysis_source")
    if analysis_source == "transition_video":
        for field_name in ("transition_video",):
            if field_name not in inputs:
                raise ValueError(f"model execution request inputs.{field_name} is required for transition video analysis")
    else:
        for field_name in ("source_a", "source_b"):
            if field_name not in inputs:
                raise ValueError(f"model execution request inputs.{field_name} is required for source analysis")
    if "result_contract" not in execution:
        raise ValueError("model execution request execution.result_contract is required")


def _validate_model_execution_result(model_result: dict[str, Any]) -> None:
    if not isinstance(model_result, dict):
        raise ValueError("model execution result must be a JSON object")
    for field_name in ("contract_type", "contract_version", "status", "execution_mode", "notes", "hint"):
        if field_name not in model_result:
            raise ValueError(f"model execution result {field_name} is required")
    if model_result.get("contract_type") != MODEL_EXECUTION_CONTRACT_TYPE:
        raise ValueError("model execution result contract_type is invalid")
    if not isinstance(model_result.get("contract_version"), int) or model_result["contract_version"] < 1:
        raise ValueError("model execution result contract_version must be a positive integer")
    if not isinstance(model_result.get("hint"), dict):
        raise ValueError("model execution result hint must be a JSON object")


def build_transition_analysis_provider_adapter(
    request: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
) -> TransitionAnalysisProvider:
    resolved_kind = _normalize_provider_value(
        request.get("kind") if isinstance(request, dict) else None,
        ANALYSIS_PROVIDER_KIND,
    )
    if resolved_kind == ANALYSIS_PROVIDER_KIND:
        return DeterministicTransitionAnalysisProvider()

    config_loaded = isinstance(configuration, dict)
    model_backed_provider = configuration.get("model_backed_provider") if config_loaded else None
    model_backed_enabled = bool(model_backed_provider.get("enabled")) if isinstance(model_backed_provider, dict) else False
    resolved_name = _normalize_provider_value(
        request.get("name") if isinstance(request, dict) else None,
        _normalize_provider_value(
            model_backed_provider.get("name") if isinstance(model_backed_provider, dict) else None,
            ANALYSIS_PROVIDER_NAME,
        ),
    )
    if model_backed_enabled:
        return ModelBackedTransitionAnalysisProvider(
            resolved_name=resolved_name,
            model_executor=build_transition_model_executor(),
        )

    return DeterministicTransitionAnalysisProvider()


def resolve_transition_analysis_provider(
    request: dict[str, Any] | None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_kind = _normalize_provider_value(
        request.get("kind") if isinstance(request, dict) else None,
        ANALYSIS_PROVIDER_KIND,
    )
    requested_name = _normalize_provider_value(
        request.get("name") if isinstance(request, dict) else None,
        ANALYSIS_PROVIDER_NAME,
    )
    requested_mode = _normalize_provider_value(
        request.get("mode") if isinstance(request, dict) else None,
        "deterministic",
    )

    config_loaded = isinstance(configuration, dict)
    config_path = configuration.get("config_path") if config_loaded else None
    config_source = configuration.get("config_source") if config_loaded else None
    model_backed_provider = configuration.get("model_backed_provider") if config_loaded else None
    model_backed_enabled = bool(model_backed_provider.get("enabled")) if isinstance(model_backed_provider, dict) else False

    if requested_kind == ANALYSIS_PROVIDER_KIND:
        return {
            "requested": {
                "kind": requested_kind,
                "name": requested_name,
                "mode": requested_mode,
            },
            "resolved": {
                "kind": ANALYSIS_PROVIDER_KIND,
                "name": ANALYSIS_PROVIDER_NAME,
                "mode": "deterministic",
            },
            "status": "resolved",
            "reason": "deterministic analyzer is built into the harness",
            "configuration": {
                "loaded": config_loaded,
                "config_path": config_path,
                "config_source": config_source,
                "model_backed_enabled": model_backed_enabled,
            },
        }

    return {
        "requested": {
            "kind": requested_kind,
            "name": requested_name,
            "mode": requested_mode,
        },
        "resolved": {
            "kind": ANALYSIS_PROVIDER_KIND,
            "name": ANALYSIS_PROVIDER_NAME,
            "mode": "deterministic",
        },
        "status": "fallback_to_deterministic",
        "reason": (
            "model-backed provider configuration is loaded but provider execution is not yet implemented"
            if model_backed_enabled
            else "model-backed provider configuration is loaded but disabled"
            if config_loaded
            else "analysis provider configuration is missing"
        ),
        "configuration": {
            "loaded": config_loaded,
            "config_path": config_path,
            "config_source": config_source,
            "model_backed_enabled": model_backed_enabled,
        },
    }


METADATA_TRANSITION_FAMILY_TO_STYLE: dict[str, str] = {
    "smooth": "seamless",
    "seamless": "seamless",
    "glitch": "glitch",
    "camera": "camera",
    "camcorder": "camcorder",
    "particle": "particle",
    "sparkle": "sparkle",
    "frame-overlay": "frame-overlay",
    "blur": "blur",
    "bokeh": "blur",
    "blur-upgrow": "blur-upgrow",
    "upgrow": "blur-upgrow",
    "blur-shakezoom": "blur-shakezoom",
    "shakezoom": "blur-shakezoom",
    "blur-diagblur": "blur-diagblur",
    "diagblur": "blur-diagblur",
    "blur-hexbokeh": "blur-hexbokeh",
    "hexbokeh": "blur-hexbokeh",
    "blur-diamondbokeh": "blur-diamondbokeh",
    "diamondbokeh": "blur-diamondbokeh",
    "blur-fadeblur": "blur-fadeblur",
    "fadeblur": "blur-fadeblur",
    "blur-rotateblur": "blur-rotateblur",
    "rotateblur": "blur-rotateblur",
    "blur-dimfade": "blur-dimfade",
    "dimfade": "blur-dimfade",
    "ui": "ui",
    "snapshot": "ui",
    "ui-app-swipe": "ui-app-swipe",
    "app-swipe": "ui-app-swipe",
    "ui-rotate-face": "ui-rotate-face",
    "rotate-face": "ui-rotate-face",
    "glitch-hdistortion": "glitch-hdistortion",
    "hdistortion": "glitch-hdistortion",
    "glitch-stretch-swipe": "glitch-stretch-swipe",
    "stretch-swipe": "glitch-stretch-swipe",
    "glitch-hdistortion2": "glitch-hdistortion2",
    "hdistortion2": "glitch-hdistortion2",
    "glitch-tunewave": "glitch-tunewave",
    "tunewave": "glitch-tunewave",
    "distortion": "distortion",
    "glitch2": "distortion",
    "generated-smooth": "generated-dissolve",
    "generated-glitch": "generated-noise",
}


def analyze_transition(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    input_kind: str,
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    reference_transition: Path | None,
    job_name: str | None,
    provider_request: dict[str, Any] | None = None,
    provider_configuration: dict[str, Any] | None = None,
    provider: TransitionAnalysisProvider | None = None,
) -> dict:
    provider_request_data = provider_request or {
        "kind": ANALYSIS_PROVIDER_KIND,
        "name": ANALYSIS_PROVIDER_NAME,
        "mode": "deterministic",
    }
    provider_resolution = resolve_transition_analysis_provider(provider_request_data, provider_configuration)
    provider_runtime = build_transition_analysis_provider_runtime(
        request=provider_request_data,
        configuration=provider_configuration,
        resolution=provider_resolution,
    )
    if provider is not None:
        hint = provider.analyze_transition(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            input_kind=input_kind,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            reference_transition=reference_transition,
            job_name=job_name,
            analyzer_inputs={
                "input_kind": input_kind,
                "style_hint": style_hint,
                "intent": intent,
                "prefer_generated": prefer_generated,
                "reference_transition": _format_optional_path(reference_transition, repo_root),
                "job_name": job_name,
            },
        )
        returned_provider = hint.get("analysis_provider")
        provider_name = "custom_provider"
        if isinstance(returned_provider, dict) and isinstance(returned_provider.get("name"), str):
            provider_name = returned_provider["name"]
        hint = _attach_transition_analysis_provider(
            hint=hint,
            provider_kind="model_backed",
            provider_name=provider_name,
        )
    else:
        execution_provider = build_transition_analysis_provider_adapter(provider_request, provider_configuration)
        hint = execution_provider.analyze_transition(
            repo_root=repo_root,
            source_a=source_a,
            source_b=source_b,
            input_kind=input_kind,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            reference_transition=reference_transition,
            job_name=job_name,
            analyzer_inputs={
                "input_kind": input_kind,
                "style_hint": style_hint,
                "intent": intent,
                "prefer_generated": prefer_generated,
                "reference_transition": _format_optional_path(reference_transition, repo_root),
                "job_name": job_name,
            },
        )
    hint.setdefault("analysis", {})
    hint["analysis_provider_request"] = provider_request_data
    hint["analysis_provider_resolution"] = provider_resolution
    hint["analysis_provider_configuration"] = provider_resolution["configuration"]
    hint["analysis_provider_runtime"] = provider_runtime
    hint["analysis_provider_adapter"] = provider_runtime["adapter"]
    hint["analysis_model_execution_contract"] = provider_runtime["execution"]["model_execution_contract"]
    return hint


def analyze_transition_video(
    repo_root: Path,
    transition_video: Path,
    input_kind: str,
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    reference_transition: Path | None,
    job_name: str | None,
    transition_window: dict[str, Any] | None,
    provider_request: dict[str, Any] | None = None,
    provider_configuration: dict[str, Any] | None = None,
    provider: TransitionAnalysisProvider | None = None,
) -> dict:
    provider_request_data = provider_request or {
        "kind": ANALYSIS_PROVIDER_KIND,
        "name": ANALYSIS_PROVIDER_NAME,
        "mode": "deterministic",
    }
    provider_resolution = resolve_transition_analysis_provider(provider_request_data, provider_configuration)
    provider_runtime = build_transition_analysis_provider_runtime(
        request=provider_request_data,
        configuration=provider_configuration,
        resolution=provider_resolution,
    )
    if provider is not None:
        hint = provider.analyze_transition_video(
            repo_root=repo_root,
            transition_video=transition_video,
            input_kind=input_kind,
            style_hint=style_hint,
            intent=intent,
            prefer_generated=prefer_generated,
            reference_transition=reference_transition,
            job_name=job_name,
            transition_window=transition_window,
            analyzer_inputs={
                "input_kind": input_kind,
                "style_hint": style_hint,
                "intent": intent,
                "prefer_generated": prefer_generated,
                "reference_transition": _format_optional_path(reference_transition, repo_root),
                "job_name": job_name,
                "analysis_source": "transition_video",
                "transition_video": _format_optional_path(transition_video, repo_root),
                "transition_window": transition_window,
            },
        )
        returned_provider = hint.get("analysis_provider")
        provider_name = "custom_provider"
        if isinstance(returned_provider, dict) and isinstance(returned_provider.get("name"), str):
            provider_name = returned_provider["name"]
        hint = _attach_transition_analysis_provider(
            hint=hint,
            provider_kind="model_backed",
            provider_name=provider_name,
        )
    else:
        execution_provider = build_transition_analysis_provider_adapter(provider_request, provider_configuration)
        if hasattr(execution_provider, "analyze_transition_video"):
            hint = execution_provider.analyze_transition_video(
                repo_root=repo_root,
                transition_video=transition_video,
                input_kind=input_kind,
                style_hint=style_hint,
                intent=intent,
                prefer_generated=prefer_generated,
                reference_transition=reference_transition,
                job_name=job_name,
                transition_window=transition_window,
                analyzer_inputs={
                    "input_kind": input_kind,
                    "style_hint": style_hint,
                    "intent": intent,
                    "prefer_generated": prefer_generated,
                    "reference_transition": _format_optional_path(reference_transition, repo_root),
                    "job_name": job_name,
                    "analysis_source": "transition_video",
                    "transition_video": _format_optional_path(transition_video, repo_root),
                    "transition_window": transition_window,
                },
            )
        else:
            raise ValueError("transition analysis provider does not support video analysis")
    hint.setdefault("analysis", {})
    hint["analysis_provider_request"] = provider_request_data
    hint["analysis_provider_resolution"] = provider_resolution
    hint["analysis_provider_configuration"] = provider_resolution["configuration"]
    hint["analysis_provider_runtime"] = provider_runtime
    hint["analysis_provider_adapter"] = provider_runtime["adapter"]
    hint["analysis_model_execution_contract"] = provider_runtime["execution"]["model_execution_contract"]
    return hint


def build_transition_analysis_artifact(
    repo_root: Path,
    source_a: Path,
    source_b: Path,
    analyzer_inputs: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any]:
    signals = hint.get("analysis", {}).get("signals", {})
    resolved_provider = hint.get("analysis_provider")
    provider_request = {
        "kind": analyzer_inputs.get("analysis_provider_kind") or ANALYSIS_PROVIDER_KIND,
        "name": analyzer_inputs.get("analysis_provider_name") or ANALYSIS_PROVIDER_NAME,
        "mode": analyzer_inputs.get("analysis_provider_mode") or "deterministic",
    }
    provider_configuration = analyzer_inputs.get("analysis_provider_configuration")
    provider_resolution = resolve_transition_analysis_provider(provider_request, provider_configuration)
    provider_runtime = build_transition_analysis_provider_runtime(
        request=provider_request,
        configuration=provider_configuration,
        resolution=provider_resolution,
    )
    recommended_plan = build_recommended_plan(
        repo_root=repo_root,
        source_a=source_a,
        source_b=source_b,
        hint_data=hint,
    )
    return {
        "artifact_type": "transition_analysis",
        "artifact_version": ANALYSIS_ARTIFACT_VERSION,
        "sources": {
            "source_a": _format_optional_path(source_a, repo_root),
            "source_b": _format_optional_path(source_b, repo_root),
            "reference_transition": hint.get("reference_transition"),
        },
        "facts": {
            "analyzer_inputs": analyzer_inputs,
            "analysis_mode": analyzer_inputs.get("analysis_mode", "deterministic_rules"),
            "analysis_provider_request": provider_request,
            "analysis_provider_resolution": provider_resolution,
            "analysis_provider_configuration": provider_configuration,
            "analysis_provider_runtime": provider_runtime,
            "analysis_provider": resolved_provider
            if isinstance(resolved_provider, dict)
            else _build_transition_analysis_provider(
                provider_kind=provider_resolution["resolved"]["kind"],
                provider_name=provider_resolution["resolved"]["name"],
                provider_mode=provider_resolution["resolved"]["mode"],
            ),
            "analysis_source": analyzer_inputs.get("analysis_source", "source_a_source_b"),
            "transition_video_analysis": {
                "source": analyzer_inputs.get("analysis_source", "source_a_source_b"),
                "analysis_engine": analyzer_inputs.get("analysis_engine", ANALYSIS_ENGINE),
                "reference_transition": analyzer_inputs.get("reference_transition"),
                "transition_video": analyzer_inputs.get("transition_video"),
                "transition_window": analyzer_inputs.get("transition_window"),
                "transition_progression": _build_transition_progression(analyzer_inputs.get("transition_window")),
            },
            "transition_summary": _build_transition_summary(signals),
            "transition_window": analyzer_inputs.get("transition_window"),
            "transition_progression": _build_transition_progression(analyzer_inputs.get("transition_window")),
            "resolved": {
                "style_hint": hint.get("style_hint"),
                "input_kind": hint.get("input_kind"),
                "job_name": hint.get("job_name"),
                "style_reason": hint.get("analysis", {}).get("style_reason"),
            },
            "signals": signals,
            "notes": hint.get("notes"),
        },
        "planning_recommendation": {
            "producer": "deterministic_analyzer",
            "analysis_engine": ANALYSIS_ENGINE,
            "auto": recommended_plan.get("auto"),
            "style": recommended_plan.get("style"),
            "input_kind": recommended_plan.get("input_kind"),
            "preset": recommended_plan.get("preset"),
            "mode": recommended_plan.get("mode"),
            "job_name": hint.get("job_name"),
            "retrieval": recommended_plan.get("retrieval"),
            "hint": hint,
        },
    }


def build_transition_analysis_provider_runtime(
    request: dict[str, Any] | None,
    configuration: dict[str, Any] | None,
    resolution: dict[str, Any],
) -> dict[str, Any]:
    requested = resolution.get("requested") if isinstance(resolution, dict) else {}
    resolved = resolution.get("resolved") if isinstance(resolution, dict) else {}
    config_state = resolution.get("configuration") if isinstance(resolution, dict) else {}

    requested_kind = requested.get("kind") if isinstance(requested, dict) else ANALYSIS_PROVIDER_KIND
    requested_mode = requested.get("mode") if isinstance(requested, dict) else "deterministic"
    selected_kind = resolved.get("kind") if isinstance(resolved, dict) else ANALYSIS_PROVIDER_KIND
    selected_name = resolved.get("name") if isinstance(resolved, dict) else ANALYSIS_PROVIDER_NAME
    selected_mode = resolved.get("mode") if isinstance(resolved, dict) else "deterministic"

    config_loaded = bool(config_state.get("loaded")) if isinstance(config_state, dict) else isinstance(configuration, dict)
    model_backed_enabled = bool(config_state.get("model_backed_enabled")) if isinstance(config_state, dict) else False
    requested_name = requested.get("name") if isinstance(requested, dict) else ANALYSIS_PROVIDER_NAME

    if requested_kind == ANALYSIS_PROVIDER_KIND or not model_backed_enabled:
        adapter_kind = ANALYSIS_PROVIDER_KIND
        adapter_name = ANALYSIS_PROVIDER_NAME
        adapter_mode = "deterministic"
        adapter_status = "deterministic_adapter"
    else:
        adapter_kind = "model_backed"
        adapter_name = requested_name
        adapter_mode = requested_mode
        adapter_status = "model_backed_adapter_skeleton"

    if requested_kind == ANALYSIS_PROVIDER_KIND:
        execution_mode = "builtin_deterministic"
        implementation_status = "ready"
    elif model_backed_enabled:
        execution_mode = "deterministic_fallback_pending_model_execution"
        implementation_status = "pending_model_execution"
    else:
        execution_mode = "deterministic_fallback"
        implementation_status = "fallback_only"

    return {
        "requested": {
            "kind": requested_kind,
            "mode": requested_mode,
        },
        "selected": {
            "kind": selected_kind,
            "name": selected_name,
            "mode": selected_mode,
        },
        "adapter": {
            "kind": adapter_kind,
            "name": adapter_name,
            "mode": adapter_mode,
            "status": adapter_status,
        },
        "configuration": {
            "loaded": config_loaded,
            "model_backed_enabled": model_backed_enabled,
        },
        "delegation": {
            "path": "deterministic" if adapter_status == "deterministic_adapter" else "model_backed_skeleton",
            "model_backed_requested": requested_kind == "model_backed",
            "model_backed_enabled": model_backed_enabled,
            "model_execution_ready": False,
        },
        "execution": {
            "entry_point": "overlay_harness.analyzer.analyze_transition",
            "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
            "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
            "implementation_status": implementation_status,
            "execution_mode": execution_mode,
            "model_execution_contract": {
                "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
                "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
                "request_contract": {
                    "provider": {
                        "kind": "model_backed",
                        "name": "openai-transition-model",
                        "mode": "vision",
                    },
                    "inputs": {
                        "repo_root": "Path",
                        "source_a": "prepared source A frame directory",
                        "source_b": "prepared source B frame directory",
                        "input_kind": "str",
                        "style_hint": "str | None",
                        "intent": "str | None",
                        "prefer_generated": "bool",
                        "reference_transition": "Path | None",
                        "job_name": "str | None",
                        "provider_request": "dict[str, Any] | None",
                        "provider_configuration": "dict[str, Any] | None",
                        "provider_adapter": "TransitionAnalysisProvider | None",
                    },
                    "analyzer_inputs": "dict[str, Any]",
                },
                "result_contract": {
                    "contract_type": MODEL_EXECUTION_CONTRACT_TYPE,
                    "contract_version": MODEL_EXECUTION_CONTRACT_VERSION,
                    "status": "str",
                    "execution_mode": "str",
                    "notes": "str",
                    "hint": "dict[str, Any]",
                },
            },
            "input_contract": {
                "repo_root": "Path",
                "source_a": "prepared source A frame directory",
                "source_b": "prepared source B frame directory",
                "input_kind": "str",
                "style_hint": "str | None",
                "intent": "str | None",
                "prefer_generated": "bool",
                "reference_transition": "Path | None",
                "job_name": "str | None",
                "provider_request": "dict[str, Any] | None",
                "provider_configuration": "dict[str, Any] | None",
                "provider_adapter": "TransitionAnalysisProvider | None",
            },
            "output_contract": {
                "analysis_provider": "dict[str, str]",
                "style_hint": "str",
                "input_kind": "str",
                "reference_transition": "str | None",
                "job_name": "str | None",
                "notes": "str",
                "analysis": "dict[str, Any]",
                "analysis_provider_runtime": "dict[str, Any]",
                "analysis_provider_adapter": "dict[str, Any]",
            },
        },
    }


def _build_transition_analysis_provider(
    provider_kind: str,
    provider_name: str,
    provider_mode: str,
) -> dict[str, str]:
    return {
        "kind": provider_kind,
        "name": provider_name,
        "mode": provider_mode,
    }


def _attach_transition_analysis_provider(
    hint: dict[str, Any],
    provider_kind: str,
    provider_name: str,
) -> dict[str, Any]:
    result = dict(hint)
    result["analysis_provider"] = _build_transition_analysis_provider(
        provider_kind=provider_kind,
        provider_name=provider_name,
        provider_mode="custom",
    )
    return result


def _normalize_provider_value(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def load_clip_metadata(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def derive_analyzer_inputs_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    transition_family = metadata.get("transition_family")
    prefer_generated = bool(metadata.get("prefer_generated"))
    style_hint = METADATA_TRANSITION_FAMILY_TO_STYLE.get(transition_family)
    if prefer_generated and transition_family == "smooth":
        style_hint = "generated-dissolve"
    style_reason = None
    if style_hint is None:
        style_hint, style_reason = _resolve_style_from_metadata_heuristics(metadata)
    else:
        style_reason = f"clip metadata transition_family was '{transition_family}'"

    return {
        "input_kind": metadata.get("input_kind") or "auto",
        "style_hint": style_hint,
        "style_reason": style_reason,
        "prefer_generated": prefer_generated,
        "reference_transition": metadata.get("reference_transition"),
        "job_name": metadata.get("job_name"),
        "notes": metadata.get("notes"),
    }


def _resolve_style_hint(
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    detected_input_kind: str,
    pair_signals: dict[str, Any],
) -> tuple[str, str]:
    if style_hint:
        return style_hint, "an explicit style hint was provided"

    normalized_intent = (intent or "").strip().lower()
    if normalized_intent:
        def _generated_or_builtin(
            generated_style: str,
            builtin_style: str,
            generated_reason: str,
            builtin_reason: str,
        ) -> tuple[str, str]:
            if prefer_generated:
                return generated_style, generated_reason
            return builtin_style, builtin_reason

        if any(token in normalized_intent for token in ("camcorder", "camera")):
            return "camcorder", "the intent mentions a camcorder or camera transition"
        if any(token in normalized_intent for token in ("particle", "sparkle", "spray")):
            return "particle", "the intent mentions particle or sparkle motion"
        if any(token in normalized_intent for token in ("frame overlay", "film roll", "overlay")):
            return "frame-overlay", "the intent mentions a frame overlay or film-roll look"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("wipe", "wipe transition")):
            return "generated-wipe", "the intent mentions a generated wipe transition"
        if "generated" in normalized_intent and any(
            token in normalized_intent for token in ("dissolve", "dissolve transition")
        ):
            return "generated-dissolve", "the intent mentions a generated dissolve transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("mask", "mask transition")):
            return "generated-mask", "the intent mentions a generated mask transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("uv shift", "uv-shift")):
            return "generated-uv-shift", "the intent mentions a generated UV shift transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("feathering", "feather")):
            return "generated-feathering", "the intent mentions a generated feathering transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("rgb split", "rgb-split")):
            return "generated-rgb-split", "the intent mentions a generated RGB split transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("noise", "noisy")):
            return "generated-noise", "the intent mentions a generated noise transition"
        if any(token in normalized_intent for token in ("wipe", "wipe transition")):
            return _generated_or_builtin(
                "generated-wipe",
                "wipe",
                "the intent mentions a generated wipe transition",
                "the intent mentions a wipe transition",
            )
        if any(token in normalized_intent for token in ("dissolve", "dissolve transition")):
            return _generated_or_builtin(
                "generated-dissolve",
                "dissolve",
                "the intent mentions a generated dissolve transition",
                "the intent mentions a dissolve transition",
            )
        if any(token in normalized_intent for token in ("mask", "mask transition")):
            return _generated_or_builtin(
                "generated-mask",
                "mask",
                "the intent mentions a generated mask transition",
                "the intent mentions a mask transition",
            )
        if any(token in normalized_intent for token in ("uv shift", "uv-shift")):
            return _generated_or_builtin(
                "generated-uv-shift",
                "uv-shift",
                "the intent mentions a generated UV shift transition",
                "the intent mentions a UV shift transition",
            )
        if any(token in normalized_intent for token in ("feathering", "feather")):
            return _generated_or_builtin(
                "generated-feathering",
                "feathering",
                "the intent mentions a generated feathering transition",
                "the intent mentions a feathering transition",
            )
        if any(token in normalized_intent for token in ("rgb split", "rgb-split")):
            return _generated_or_builtin(
                "generated-rgb-split",
                "rgb-split",
                "the intent mentions a generated RGB split transition",
                "the intent mentions an RGB split transition",
            )
        if any(token in normalized_intent for token in ("noise", "noisy")):
            return _generated_or_builtin(
                "generated-noise",
                "noise",
                "the intent mentions a generated noise transition",
                "the intent mentions a noise transition",
            )
        if any(token in normalized_intent for token in ("blur", "bokeh", "soft focus")):
            return "blur", "the intent mentions a blur or bokeh transition"
        if any(token in normalized_intent for token in ("upgrow", "up grow")):
            return "blur-upgrow", "the intent mentions the upgrow blur wrapper"
        if any(token in normalized_intent for token in ("shakezoom", "shake zoom")):
            return "blur-shakezoom", "the intent mentions the shakezoom blur wrapper"
        if any(token in normalized_intent for token in ("diagblur", "diagonal blur")):
            return "blur-diagblur", "the intent mentions the diagonal blur wrapper"
        if any(token in normalized_intent for token in ("hexbokeh", "hex bokeh")):
            return "blur-hexbokeh", "the intent mentions the hex bokeh wrapper"
        if any(token in normalized_intent for token in ("diamondbokeh", "diamond bokeh")):
            return "blur-diamondbokeh", "the intent mentions the diamond bokeh wrapper"
        if any(token in normalized_intent for token in ("fadeblur", "fade blur")):
            return "blur-fadeblur", "the intent mentions the fade blur wrapper"
        if any(token in normalized_intent for token in ("rotateblur", "rotate blur")):
            return "blur-rotateblur", "the intent mentions the rotate blur wrapper"
        if any(token in normalized_intent for token in ("dimfade", "dim fade")):
            return "blur-dimfade", "the intent mentions the dim fade wrapper"
        if any(token in normalized_intent for token in ("app swipe", "app-swipe")):
            return "ui-app-swipe", "the intent mentions the app swipe UI wrapper"
        if any(token in normalized_intent for token in ("rotate face", "rotateface")):
            return "ui-rotate-face", "the intent mentions the rotate face UI wrapper"
        if any(token in normalized_intent for token in ("hdistortion", "h distortion")):
            return "glitch-hdistortion", "the intent mentions the hdistortion glitch wrapper"
        if any(token in normalized_intent for token in ("stretch swipe", "stretch-swipe")):
            return "glitch-stretch-swipe", "the intent mentions the stretch swipe glitch wrapper"
        if any(token in normalized_intent for token in ("hdistortion2", "h distortion 2")):
            return "glitch-hdistortion2", "the intent mentions the hdistortion2 glitch wrapper"
        if any(token in normalized_intent for token in ("tunewave", "tune wave")):
            return "glitch-tunewave", "the intent mentions the tune wave glitch wrapper"
        if any(token in normalized_intent for token in ("ui", "snapshot", "screen")):
            return "ui", "the intent mentions a UI or snapshot-style transition"
        if any(token in normalized_intent for token in ("distortion", "distort", "warping", "warp")):
            return "distortion", "the intent mentions a distortion-style transition"
        if "generated" in normalized_intent and "glitch" in normalized_intent:
            return "generated-noise", "the intent mentions a generated glitch transition"
        if "generated" in normalized_intent and any(
            token in normalized_intent for token in ("smooth", "seamless", "slide", "sliding")
        ):
            return "generated-dissolve", "the intent mentions a generated and smooth or sliding transition"
        if "glitch" in normalized_intent:
            if prefer_generated:
                return "generated-noise", "the intent mentions glitch and generated output was preferred"
            return "glitch", "the intent mentions glitch"
        if any(token in normalized_intent for token in ("smooth", "seamless", "slide", "sliding")):
            if prefer_generated:
                return "generated-dissolve", "the intent mentions a smooth or sliding transition and generated output was preferred"
            return "seamless", "the intent mentions a smooth or sliding transition"

    if prefer_generated:
        if pair_signals["combined_visual_energy"] == "high":
            return "generated-noise", "generated output was preferred and local frame signals indicate high visual energy"
        if detected_input_kind == "fixture":
            return "generated-dissolve", "generated output was preferred and fixture inputs are better served by a smooth generated baseline"
        return "generated-rgb-split", "generated output was preferred and real or custom inputs default to a visible generated baseline"

    if pair_signals["combined_visual_energy"] == "high" or pair_signals["combined_motion_level"] == "high":
        return "glitch", "local frame signals indicate high motion or visual energy"

    if pair_signals["detected_static_pair"]:
        return "seamless", "local frame signals indicate a static or low-motion pair that fits the smooth baseline"

    return "seamless", "no stronger signal was provided, so the analyzer chose the safest baseline transition"


def _resolve_video_style_hint(
    transition_video: Path,
    style_hint: str | None,
    intent: str | None,
    prefer_generated: bool,
    transition_window: dict[str, Any] | None,
) -> tuple[str, str]:
    if style_hint:
        return style_hint, "an explicit style hint was provided"

    normalized_intent = (intent or "").strip().lower()
    if normalized_intent:
        if any(token in normalized_intent for token in ("camcorder", "camera")):
            return "camcorder", "the intent mentions a camcorder or camera transition"
        if any(token in normalized_intent for token in ("particle", "sparkle", "spray")):
            return "particle", "the intent mentions particle or sparkle motion"
        if any(token in normalized_intent for token in ("frame overlay", "film roll", "overlay")):
            return "frame-overlay", "the intent mentions a frame overlay or film-roll look"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("wipe", "wipe transition")):
            return "generated-wipe", "the intent mentions a generated wipe transition"
        if "generated" in normalized_intent and any(
            token in normalized_intent for token in ("dissolve", "dissolve transition")
        ):
            return "generated-dissolve", "the intent mentions a generated dissolve transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("mask", "mask transition")):
            return "generated-mask", "the intent mentions a generated mask transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("uv shift", "uv-shift")):
            return "generated-uv-shift", "the intent mentions a generated UV shift transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("feathering", "feather")):
            return "generated-feathering", "the intent mentions a generated feathering transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("rgb split", "rgb-split")):
            return "generated-rgb-split", "the intent mentions a generated RGB split transition"
        if "generated" in normalized_intent and any(token in normalized_intent for token in ("noise", "noisy")):
            return "generated-noise", "the intent mentions a generated noise transition"
        if any(token in normalized_intent for token in ("wipe", "wipe transition")):
            return "generated-wipe" if prefer_generated else "wipe", "the intent mentions a wipe transition"
        if any(token in normalized_intent for token in ("dissolve", "dissolve transition")):
            return "generated-dissolve" if prefer_generated else "dissolve", "the intent mentions a dissolve transition"
        if any(token in normalized_intent for token in ("mask", "mask transition")):
            return "generated-mask" if prefer_generated else "mask", "the intent mentions a mask transition"
        if any(token in normalized_intent for token in ("uv shift", "uv-shift")):
            return "generated-uv-shift" if prefer_generated else "uv-shift", "the intent mentions a UV shift transition"
        if any(token in normalized_intent for token in ("feathering", "feather")):
            return "generated-feathering" if prefer_generated else "feathering", "the intent mentions a feathering transition"
        if any(token in normalized_intent for token in ("rgb split", "rgb-split")):
            return "generated-rgb-split" if prefer_generated else "rgb-split", "the intent mentions an RGB split transition"
        if any(token in normalized_intent for token in ("noise", "noisy")):
            return "generated-noise" if prefer_generated else "noise", "the intent mentions a noise transition"
        if any(token in normalized_intent for token in ("glitch", "distortion", "warp")):
            return "generated-noise" if prefer_generated else "glitch", "the intent mentions a glitch or distortion transition"
        if any(token in normalized_intent for token in ("smooth", "seamless", "slide", "sliding")):
            return "generated-dissolve" if prefer_generated else "seamless", "the intent mentions a smooth or sliding transition"

    name = transition_video.name.lower()
    if "glitch" in name:
        return "generated-noise" if prefer_generated else "glitch", "the transition video filename mentions glitch"
    if any(token in name for token in ("seamless", "smooth", "slide")):
        return "generated-dissolve" if prefer_generated else "seamless", "the transition video filename mentions a smooth or sliding transition"
    if "wipe" in name:
        return "generated-wipe" if prefer_generated else "wipe", "the transition video filename mentions wipe"
    if "dissolve" in name:
        return "generated-dissolve" if prefer_generated else "dissolve", "the transition video filename mentions dissolve"
    if "mask" in name:
        return "generated-mask" if prefer_generated else "mask", "the transition video filename mentions mask"
    if "rgb" in name:
        return "generated-rgb-split" if prefer_generated else "rgb-split", "the transition video filename mentions rgb split"
    if "noise" in name:
        return "generated-noise" if prefer_generated else "noise", "the transition video filename mentions noise"

    if isinstance(transition_window, dict):
        detected_frame_count = transition_window.get("detected_frame_count")
        frame_count = transition_window.get("frame_count")
        if isinstance(detected_frame_count, int) and isinstance(frame_count, int) and frame_count > 0:
            coverage_ratio = detected_frame_count / frame_count
            if coverage_ratio >= 0.85:
                return (
                    "generated-dissolve" if prefer_generated else "seamless",
                    "the transition window covers most frames and no stronger video signal was provided",
                )

    if prefer_generated:
        return "generated-dissolve", "generated output was preferred and the video did not provide a stronger signal"

    return "seamless", "the transition video did not provide a stronger signal, so the analyzer chose the smooth baseline"


def _build_transition_window_analysis(transition_window: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(transition_window, dict):
        return {
            "frame_count": None,
            "detected_start_frame": None,
            "detected_end_frame": None,
            "detected_frame_count": None,
            "message": None,
        }

    return {
        "frame_count": transition_window.get("frame_count"),
        "detected_start_frame": transition_window.get("detected_start_frame"),
        "detected_end_frame": transition_window.get("detected_end_frame"),
        "detected_frame_count": transition_window.get("detected_frame_count"),
        "message": transition_window.get("message"),
    }


def _resolve_style_from_metadata_heuristics(metadata: dict[str, Any]) -> tuple[str, str]:
    motion_level = metadata.get("motion_level")
    visual_energy = metadata.get("visual_energy")
    prefer_generated = bool(metadata.get("prefer_generated"))

    if motion_level == "high" or visual_energy == "high":
        if prefer_generated:
            return "generated-noise", "metadata indicates high motion or visual energy and generated output was preferred"
        return "glitch", "metadata indicates high motion or visual energy"

    if prefer_generated:
        return "generated-rgb-split", "metadata did not signal a glitch case and generated output was preferred"

    return "seamless", "metadata did not signal a glitch case, so the analyzer chose the smooth baseline"


def inspect_prepared_input(input_dir: Path) -> dict[str, Any]:
    manifest = _load_prepare_manifest(input_dir)
    frame_files = sorted(
        file_path
        for file_path in input_dir.iterdir()
        if file_path.is_file() and file_path.name.startswith("frame_")
    )

    sample_files = _sample_frame_files(frame_files, limit=12)
    file_sizes = [file_path.stat().st_size for file_path in sample_files]
    hashes = [_hash_file(file_path) for file_path in sample_files]

    distinct_hash_count = len(set(hashes))
    distinct_size_count = len(set(file_sizes))
    average_size = int(sum(file_sizes) / len(file_sizes)) if file_sizes else 0
    size_range = (max(file_sizes) - min(file_sizes)) if file_sizes else 0

    return {
        "path": str(input_dir),
        "manifest_mode": manifest.get("mode") if manifest else None,
        "format": manifest.get("format") if manifest else None,
        "frame_count": manifest.get("frame_count") if manifest else len(frame_files),
        "sampled_frame_count": len(sample_files),
        "distinct_hash_count": distinct_hash_count,
        "distinct_size_count": distinct_size_count,
        "average_sample_size": average_size,
        "sample_size_range": size_range,
        "static_sequence": distinct_hash_count <= 1,
        "motion_level": _classify_motion_level(distinct_hash_count, len(sample_files)),
        "visual_energy": _classify_visual_energy(distinct_hash_count, distinct_size_count, size_range, average_size),
    }


def summarize_pair_signals(source_a: dict[str, Any], source_b: dict[str, Any]) -> dict[str, Any]:
    combined_motion_level = _max_level(source_a["motion_level"], source_b["motion_level"])
    combined_visual_energy = _max_level(source_a["visual_energy"], source_b["visual_energy"])
    return {
        "source_a": source_a,
        "source_b": source_b,
        "combined_motion_level": combined_motion_level,
        "combined_visual_energy": combined_visual_energy,
        "detected_static_pair": bool(source_a["static_sequence"] and source_b["static_sequence"]),
    }


def _load_prepare_manifest(input_dir: Path) -> dict[str, Any] | None:
    manifest_file = input_dir / "prepare_video_manifest.json"
    if not manifest_file.exists():
        return None

    with manifest_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sample_frame_files(frame_files: list[Path], limit: int) -> list[Path]:
    if len(frame_files) <= limit:
        return frame_files

    indexes = {round(index * (len(frame_files) - 1) / (limit - 1)) for index in range(limit)}
    return [frame_files[index] for index in sorted(indexes)]


def _hash_file(file_path: Path) -> str:
    digest = hashlib.sha1()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _classify_motion_level(distinct_hash_count: int, sample_count: int) -> str:
    if sample_count <= 1 or distinct_hash_count <= 1:
        return "low"

    diversity_ratio = distinct_hash_count / sample_count
    if diversity_ratio >= 0.8:
        return "high"
    if diversity_ratio >= 0.35:
        return "medium"
    return "low"


def _classify_visual_energy(
    distinct_hash_count: int,
    distinct_size_count: int,
    size_range: int,
    average_size: int,
) -> str:
    if distinct_hash_count <= 1 and distinct_size_count <= 1:
        return "low"

    if average_size > 0 and size_range / average_size >= 0.25:
        return "high"
    if distinct_hash_count >= 6 or distinct_size_count >= 6:
        return "high"
    if distinct_hash_count >= 3 or distinct_size_count >= 3:
        return "medium"
    return "low"


def _max_level(level_a: str, level_b: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    return level_a if rank[level_a] >= rank[level_b] else level_b


def _format_optional_path(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return str(resolved)


def _build_transition_summary(signals: dict[str, Any]) -> dict[str, Any]:
    source_a = signals.get("source_a", {}) if isinstance(signals, dict) else {}
    source_b = signals.get("source_b", {}) if isinstance(signals, dict) else {}
    return {
        "source_a_motion_level": source_a.get("motion_level"),
        "source_a_visual_energy": source_a.get("visual_energy"),
        "source_b_motion_level": source_b.get("motion_level"),
        "source_b_visual_energy": source_b.get("visual_energy"),
        "combined_motion_level": signals.get("combined_motion_level") if isinstance(signals, dict) else None,
        "combined_visual_energy": signals.get("combined_visual_energy") if isinstance(signals, dict) else None,
        "detected_static_pair": signals.get("detected_static_pair") if isinstance(signals, dict) else None,
    }


def _build_transition_progression(transition_window: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(transition_window, dict):
        return {
            "window_span_frames": None,
            "window_midpoint_frame": None,
            "window_coverage_ratio": None,
            "window_start_progress": None,
            "window_end_progress": None,
            "window_message": None,
        }

    frame_count = transition_window.get("frame_count")
    detected_start_frame = transition_window.get("detected_start_frame")
    detected_end_frame = transition_window.get("detected_end_frame")
    detected_frame_count = transition_window.get("detected_frame_count")
    midpoint_frame = None
    if isinstance(detected_start_frame, int) and isinstance(detected_end_frame, int):
        midpoint_frame = detected_start_frame + ((detected_end_frame - detected_start_frame) // 2)
    coverage_ratio = None
    if isinstance(frame_count, int) and frame_count > 0 and isinstance(detected_frame_count, int):
        coverage_ratio = round(detected_frame_count / frame_count, 4)
    start_progress = None
    end_progress = None
    if isinstance(frame_count, int) and frame_count > 1:
        if isinstance(detected_start_frame, int):
            start_progress = round(detected_start_frame / (frame_count - 1), 4)
        if isinstance(detected_end_frame, int):
            end_progress = round(detected_end_frame / (frame_count - 1), 4)

    return {
        "window_span_frames": detected_frame_count,
        "window_midpoint_frame": midpoint_frame,
        "window_coverage_ratio": coverage_ratio,
        "window_start_progress": start_progress,
        "window_end_progress": end_progress,
        "window_message": transition_window.get("message"),
    }
