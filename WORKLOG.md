# WORKLOG

This file is the persistent session handoff for the `harness/` project. Keep it short and restart-friendly.

## Current Objective

Milestone 1 acceptance is frozen in `MILESTONE1_ACCEPTANCE.md`. Milestone 2 retrieval coverage is complete. Milestone 3 is focused on a fixed generated-effect grammar.

## Last Completed

- Added the first Milestone 3 grammar slice: canonical generated styles now resolve through deterministic planner mappings and unsupported generated styles are rejected explicitly.
- Added generated-style aliases like `generated-wipe`, `generated-rgb-split`, and `generated-dissolve` so the approved grammar can be addressed with consistent naming.
- Added analyzer regressions for generated intent phrases, `prefer_generated` behavior, and metadata-derived generated preference.
- Kept generated preference inside the approved grammar by using `generated-noise`, `generated-dissolve`, and `generated-rgb-split` as the generic fallback aliases.
- Kept the worklog restart-friendly by trimming older completion history.

Milestone 2 is complete on the harness side: retrieval is wired through planning, fallback metadata is explicit, the source manifest and planner vocabulary are aligned, and the remaining retrieval improvements were reduced to source-backed alias coverage and tie-breaking.

## Next Implementation Step

Continue Milestone 3 grammar coverage:

1. Keep unsupported generated styles out of auto-plan resolution until they are explicitly added to the grammar.
2. Keep sample/test artifacts grouped under `harness/work/tests/` unless a caller overrides `--output-root`.

## Why This Is Next

- Retrieval metadata now flows through planning, run reports, top-level run output, plan-comparison reports, smoke-test batch summaries, the plan-job result payload, the `validate`/`prepare` command results, and the new end-to-end `flow` command. The remaining gap is narrowing generated outputs to a small, explicit grammar so future work stays deterministic.

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
