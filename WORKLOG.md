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
- Tightened job validation so `inputs.reference_transition` must resolve to a valid prepared reference artifact with a matching manifest and frame set.
- Added explicit evaluator summary fields to `run_report.json` so render and score status are easier to distinguish.
- Promoted `run_report.json` to a versioned contract with `report_type` and `report_version`.
- Propagated score failures into the top-level run status and summary.
- Promoted prepared-reference dimension mismatches from warnings to validation errors.

## Next Implementation Step

Define the Milestone 1 acceptance boundary:

1. Decide which harness commands and report fields are mandatory for the evaluator milestone.
2. Decide whether any further reference metadata checks are required before treating Milestone 1 as complete.
3. Decide whether the evaluator contract should be frozen now or after one more smoke-test pass.

## Why This Is Next

- The reference metadata policy is now strict, so the remaining Milestone 1 work is finalizing the acceptance boundary rather than changing validator behavior.

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
