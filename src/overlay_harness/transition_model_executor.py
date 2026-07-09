from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIChatTransitionModelExecutor:
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout_seconds: int = 60,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.executor_source = f"env:openai:{self.model_name}"

    @classmethod
    def from_environment(cls) -> "OpenAIChatTransitionModelExecutor":
        model_name = os.environ.get("HARNESS_TRANSITION_MODEL_NAME") or os.environ.get("OPENAI_TRANSITION_MODEL")
        if not model_name:
            raise ValueError(
                "HARNESS_TRANSITION_MODEL_EXECUTOR=openai requires HARNESS_TRANSITION_MODEL_NAME or OPENAI_TRANSITION_MODEL"
            )

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("HARNESS_TRANSITION_MODEL_EXECUTOR=openai requires OPENAI_API_KEY")

        base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        timeout_value = os.environ.get("HARNESS_TRANSITION_MODEL_TIMEOUT_SECONDS", "60")
        try:
            timeout_seconds = int(timeout_value)
        except ValueError as exc:
            raise ValueError("HARNESS_TRANSITION_MODEL_TIMEOUT_SECONDS must be a positive integer") from exc
        if timeout_seconds <= 0:
            raise ValueError("HARNESS_TRANSITION_MODEL_TIMEOUT_SECONDS must be a positive integer")

        return cls(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def execute_model_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        try:
            hint = self._invoke_chat_completion(model_request)
            return {
                "contract_type": "transition_analysis_model_execution",
                "contract_version": 1,
                "status": "model_execution_succeeded",
                "execution_mode": "openai_chat_completions",
                "notes": "OpenAI chat completion executor returned a structured transition hint",
                "executor_source": self.executor_source,
                "hint": hint,
            }
        except Exception as exc:
            hint = self._fallback_hint(model_request)
            return {
                "contract_type": "transition_analysis_model_execution",
                "contract_version": 1,
                "status": "delegated_to_deterministic_fallback",
                "execution_mode": "openai_chat_completions_fallback",
                "notes": f"OpenAI chat completion executor fell back to the deterministic analyzer: {exc}",
                "executor_source": self.executor_source,
                "hint": hint,
            }

    def _invoke_chat_completion(self, model_request: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(model_request)
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a transition analysis executor for a video harness. "
                        "Return only valid JSON with no markdown or code fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise ValueError(
                f"OpenAI chat completions request failed with HTTP {exc.code}: {error_body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise ValueError(f"OpenAI chat completions request failed: {exc.reason}") from exc

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI chat completion response did not include choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("OpenAI chat completion response choice was not a JSON object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenAI chat completion response did not include a message object")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI chat completion response message.content was empty")

        hint = self._parse_json_object(content)
        self._validate_hint(hint)
        return hint

    def _build_prompt(self, model_request: dict[str, Any]) -> str:
        execution = model_request.get("execution", {})
        result_contract = execution.get("result_contract", {}) if isinstance(execution, dict) else {}
        input_contract = execution.get("input_contract", {}) if isinstance(execution, dict) else {}
        prompt_payload = {
            "contract_type": model_request.get("contract_type"),
            "contract_version": model_request.get("contract_version"),
            "provider": model_request.get("provider"),
            "inputs": model_request.get("inputs"),
            "analyzer_inputs": model_request.get("analyzer_inputs"),
            "execution": {
                "expected_status": execution.get("expected_status") if isinstance(execution, dict) else None,
                "input_contract": input_contract,
                "result_contract": result_contract,
            },
        }
        return (
            "Produce a JSON object that matches the transition-analysis hint contract.\n"
            "The output must include analysis_source, style_hint, input_kind, reference_transition, job_name, notes, and analysis.\n"
            "If the input analysis_source is transition_video, also include transition_video, transition_video_analysis, "
            "transition_summary, transition_window, and transition_progression.\n"
            "Keep analysis as a JSON object and make notes concise.\n"
            "Input request JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, sort_keys=True)}"
        )

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].lstrip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise ValueError("OpenAI chat completion response did not contain a JSON object")
        parsed = json.loads(stripped[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI chat completion response JSON must be an object")
        return parsed

    def _validate_hint(self, hint: dict[str, Any]) -> None:
        for field_name in ("analysis_source", "style_hint", "input_kind", "reference_transition", "job_name", "notes", "analysis"):
            if field_name not in hint:
                raise ValueError(f"OpenAI chat completion hint missing required field: {field_name}")
        if not isinstance(hint.get("analysis"), dict):
            raise ValueError("OpenAI chat completion hint.analysis must be a JSON object")
        if hint.get("analysis_source") == "transition_video":
            for field_name in ("transition_video", "transition_video_analysis", "transition_summary", "transition_window", "transition_progression"):
                if field_name not in hint:
                    raise ValueError(f"OpenAI chat completion hint missing transition video field: {field_name}")

    def _fallback_hint(self, model_request: dict[str, Any]) -> dict[str, Any]:
        from .analyzer import DeterministicTransitionAnalysisProvider

        deterministic_provider = DeterministicTransitionAnalysisProvider()
        inputs = model_request["inputs"]
        analysis_source = inputs.get("analysis_source") if isinstance(inputs, dict) else None
        transition_window = inputs.get("transition_window") if isinstance(inputs, dict) else None
        if analysis_source == "transition_video":
            return deterministic_provider.analyze_transition_video(
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

        return deterministic_provider.analyze_transition(
            repo_root=Path(inputs["repo_root"]),
            source_a=Path(inputs["source_a"]),
            source_b=Path(inputs["source_b"]),
            input_kind=inputs["input_kind"],
            style_hint=inputs["style_hint"],
            intent=inputs["intent"],
            prefer_generated=bool(inputs["prefer_generated"]),
            reference_transition=Path(inputs["reference_transition"]) if inputs["reference_transition"] else None,
            job_name=inputs["job_name"],
            analyzer_inputs=model_request["analyzer_inputs"],
        )
