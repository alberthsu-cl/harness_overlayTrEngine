# WORKLOG

This file is the persistent session handoff for the `harness/` project. Update it at the end of each meaningful work slice so a restart does not lose the exact resume point.

## Current Objective

Milestone 1 acceptance is now frozen in `MILESTONE1_ACCEPTANCE.md`. The next major objective is Milestone 2: index existing `OverlayTrPlugInFx` transitions and use retrieval before generation.

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
- Froze the Milestone 1 acceptance boundary in `MILESTONE1_ACCEPTANCE.md`.
- Added a deterministic effect catalog and routed generated styles through retrieval before placeholder fallback.
- Surfaced planner retrieval metadata into analyzer recommendations, planned jobs, and run reports.
- Marked placeholder routing as an explicit fallback in planner metadata and run evaluation summaries.
- Moved effect catalog indexing to a checked-in source manifest so future registrations can be added without code changes.
- Added manifest validation and a `--source-manifest` override for `index-effects`.
- Made generated catalogs self-describing with source-manifest provenance and registration counts.
- Added `audit-effects` to report baseline-vs-manifest gaps for the source-driven catalog.
- Made `audit-effects` a non-zero exit-code gate when the source manifest drifts from the baseline.
- Validated source-manifest `source_documents` so missing spec references fail fast.
- Tightened source-manifest validation for effect source type, required fields, and generated fallback wiring.
- Added `--output` support for `audit-effects` so the audit can be stored as a JSON artifact.
- Added SHA-256 provenance to catalog and audit reports so manifest drift is traceable.
- Tightened source-manifest validation so builtin entries cannot carry fallback metadata and generated entries must carry it.
- Added checked-in `overlaytrengine` source provenance to the builtin effect registrations so the catalog now points at real transition source in addition to harness examples.

## Next Implementation Step

Expand Milestone 2 retrieval coverage:

1. Add more real transition registrations from `overlaytrengine` when there is a clear style mapping.
2. Keep generated-placeholder modes as the fallback path only, with explicit fallback metadata.

## Why This Is Next

- The first retrieval slice is now in place, and retrieval metadata now flows through planning, run reports, and fallback metadata. Remaining Milestone 2 work is source-manifest coverage and fallback discipline.

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
