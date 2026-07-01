# WORKLOG

This file is the persistent session handoff for the `harness/` project. Update it at the end of each meaningful work slice so a restart does not lose the exact resume point.

## Current Objective

Harden scoring consistency for prepared reference transitions so frame alignment remains explicit and robust when the detected transition window is shorter or longer than the original target length.

## Last Completed

- Added prepared reference-transition extraction from a sample video.
- Synced planner frame count to `reference_transition_manifest.json` when `--reference-transition` is used.
- Added automatic post-run similarity scoring and score report output.
- Tightened scoring so prepared reference manifests enforce exact frame-count alignment and write manifest-backed alignment metadata into `similarity_score.json`.
- Added automated `unittest` coverage for prepared-reference scoring alignment, mismatch failures, and non-prepared fallback behavior.

## Next Implementation Step

Tighten validation for `inputs.reference_transition`:

1. Validate prepared reference manifests earlier in the job lifecycle when `inputs.reference_transition` is present.
2. Fail fast on missing or malformed prepared-reference manifests for workflows that expect reliable scoring.
3. Keep `run_report.json` as the stable evaluator summary entrypoint.

## Why This Is Next

- Scoring behavior is now covered directly, so the next Milestone 1 gap is earlier validation and clearer evaluator contracts around `reference_transition` inputs.

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
- Do not patch `overlaytrengine` yet; this slice should stay inside `harness/`.
- If future work needs FX naming conventions, registration changes, or generated source locations, ask before guessing as required by `AGENTS.md`.
