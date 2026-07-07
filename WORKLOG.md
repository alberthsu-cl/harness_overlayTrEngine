# WORKLOG

This file is the persistent session handoff for the `harness/` project. Update it at the end of each meaningful work slice so a restart does not lose the exact resume point.

## Current Objective

Milestone 1 acceptance is now frozen in `MILESTONE1_ACCEPTANCE.md`. Milestone 2 retrieval coverage is complete; Milestone 3 is to define and enforce a fixed generated-effect grammar before any broader effect generation work.

## Last Completed

- Added display-name aliases from `FxInfo.h` for `slide-07`, `camera-02`, `sparkle-01`, and `film-roll-01` so the source-backed vocabulary is closer to the plugin’s own labels.
- Added the `glitch-04` source-backed alias for the builtin glitch effect and verified it resolves through retrieval and auto-plan.
- Added the `glitch-distortion` retrieval alias for the existing source-backed `builtin-glitch-distortion` effect and verified it resolves through the catalog and auto-plan path.
- Added source-backed display-name aliases for `blur-dollarbokeh`, `glitch-hdistor1`, and `ui-snapshot` so the planner can resolve more of the plugin's literal `FxInfo.h` names.
- Refined retrieval tie-breaking to prefer the candidate whose matching style appears earlier in the source manifest when priority is otherwise equal.
- Added the Milestone 3 checklist so the next slice stays bounded to the fixed generated-effect grammar.
- Added the first Milestone 3 grammar slice: canonical generated styles now resolve through deterministic planner mappings and unsupported generated styles are rejected explicitly.
- Added tests for the approved generated grammar styles and unsupported-style rejection.
- Added generated-style aliases like `generated-wipe` and `generated-rgb-split` so the approved grammar can be addressed with consistent naming.
- Added an analyzer regression test for generated intent phrases that map to the new generated-style aliases.
- Made the analyzer prefer the generated alias spelling when `prefer_generated` is set for an approved grammar style.
- Moved the generic generated fallback onto approved aliases like `generated-noise`, `generated-dissolve`, and `generated-rgb-split` so metadata-driven generated preference stays inside the grammar.
- Added a metadata regression test for the approved generated fallback aliases.
- Moved the explicit generated-glitch and generated-smooth intent branches onto approved aliases so the analyzer stays inside the Milestone 3 grammar.
- Added analyzer regressions for generated glitch and smooth intent phrases using the approved alias names.
- Made smooth-family metadata prefer `generated-dissolve` when generated output is requested so the metadata path stays inside the approved grammar.
- Added a metadata regression for generated-preferred smooth-family inputs.

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
