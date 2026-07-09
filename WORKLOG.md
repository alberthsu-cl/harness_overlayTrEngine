# WORKLOG

This file is the persistent session handoff for the `harness/` project. Keep it short and restart-friendly.

## Current Objective

Milestone 1 acceptance is frozen in `MILESTONE1_ACCEPTANCE.md`. Milestone 2 retrieval coverage is complete. Milestone 3 rule-based single-pass flow is stable. Milestone 4 is closed as the video-backed transition analysis/report path.

Milestone 5 is now the model-inference phase for video analysis. It focuses on replacing the deterministic fallback with a real model-backed analysis path while keeping the current artifact contracts stable.

Milestone 6 will cover model inference for compile-gated C++/HLSL generation.

Milestone 7 will cover the retry and validation loop that makes generation robust.

## Last Completed

- Added a built-in OpenAI chat-completions executor path behind `HARNESS_TRANSITION_MODEL_EXECUTOR=openai`.
- Threaded the selected executor source through the model execution request/result contract and provider-runtime summaries.
- Added tests for the built-in executor selection and the ready-state reporting path.

## Next Implementation Step

Continue from the model-backed boundary by wiring the next real request field through the flow path, then verify it in `flow` and `sample-video`.

## Why This Is Next

- The model-executor boundary is now concrete, but the flow still needs the next request fields and follow-on reporting to be wired through the same stable contract.

## Resume Commands

Run from `D:\AI_Harness`:

```powershell
git -C harness status --short
git -C harness log --oneline -5
py -3 harness/src/main.py --help
```

Then inspect the likely implementation points:

```powershell
rg -n "plan-job|preset|mode|generated" harness/src/overlay_harness
Get-Content harness/src/overlay_harness/planner.py
Get-Content harness/src/overlay_harness/cli.py
```

## Working Notes

- Keep the catalog deterministic and JSON-stable.
- Prefer scanning checked-in source and project metadata over any runtime-only discovery.
- Keep this slice inside `harness/` unless the user explicitly asks to modify `overlaytrengine`.
- If future work needs FX naming conventions, registration changes, or generated source locations, ask before guessing as required by `AGENTS.md`.
- Model-backed analysis is now the next milestone after the rule-based single-pass flow.
