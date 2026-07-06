# WORKLOG

This file is the persistent session handoff for the `harness/` project. Update it at the end of each meaningful work slice so a restart does not lose the exact resume point.

## Current Objective

Milestone 1 acceptance is now frozen in `MILESTONE1_ACCEPTANCE.md`. Milestone 2 retrieval coverage is complete; Milestone 3 is to define and enforce a fixed generated-effect grammar before any broader effect generation work.

## Last Completed

- Added display-name aliases from `FxInfo.h` for `slide-07`, `camera-02`, `sparkle-01`, and `film-roll-01` so the source-backed vocabulary is closer to the plugin’s own labels.
- Added the `glitch-04` source-backed alias for the builtin glitch effect and verified it resolves through retrieval and auto-plan.
- Added a guardrail test that keeps planner style aliases aligned with the builtin source manifest style hints.
- Added the `glitch-distortion` retrieval alias for the existing source-backed `builtin-glitch-distortion` effect and verified it resolves through the catalog and auto-plan path.
- Added explicit fallback metadata for `generated-*-placeholder` planning modes so direct placeholder requests are recorded as fallback usage instead of looking like ordinary plans.
- Propagated the enriched planning object through `plan-job`, `flow`, and `sample-video` so the emitted reports reflect the fallback metadata.
- Added a regression test for explicit generated-placeholder planning metadata.
- Added `sample-video --style glitch` and `sample-video --force-mode builtin-glitch` support so synthetic reference MP4s can be generated either from planner hints or from a forced builtin mode.
- Documented the new sample-video invocation options in the README.
- Consolidated the smoke-test retrieval rollups behind a shared helper while keeping the run and validation batch summaries intact.
- Propagated retrieval diagnostics into the recommended-plan, run-evaluation, top-level run output, plan-comparison report, smoke-test batch report, and top-level plan-job result payload.
- Added a source-manifest audit test that verifies every builtin registration matches the live `overlaytrengine/OverlayTrPlugInFx/FxInfo.h` mappings.
- Added generated-seamless fallback symmetry tests so the placeholder path is covered the same way as generated-glitch.
- Added a new `flow` command that runs the end-to-end transition pipeline from transition video plus prepared source A/B inputs and writes a single report.
- Added README invocation examples for the new `flow` command.
- Added MP4 demo encoding for successful render outputs so runs and flows now leave an easy-to-share `artifacts/rendered.mp4`.
- Added a clean benchmark A/B fixture pair under `examples/inputs/source_a_clean` and `examples/inputs/source_b_clean` from `Food.jpg` and `Landscape01.jpg`.
- Added a `sample-video` command that renders a synthetic reference MP4 from either an explicit `fx_id` or the current A/B-driven planner and copies it to a requested output path.
- Defaulted the sample-video intermediate workspace to `harness/work/tests/` so test artifacts are easier to find.
- Trimmed the worklog handoff so it stays restart-friendly.

- Added source-backed display-name aliases for `blur-dollarbokeh`, `glitch-hdistor1`, and `ui-snapshot` so the planner can resolve more of the plugin's literal `FxInfo.h` names.
- Refined retrieval tie-breaking to prefer the candidate whose matching style appears earlier in the source manifest when priority is otherwise equal.

Milestone 2 is complete on the harness side: retrieval is wired through planning, fallback metadata is explicit, the source manifest and planner vocabulary are aligned, and the remaining retrieval improvements were reduced to source-backed alias coverage and tie-breaking.

## Next Implementation Step

Define Milestone 3 as a bounded grammar layer:

1. Add a canonical generated-effect grammar for the approved styles: wipe, dissolve, mask, UV shift, feathering, RGB split, and noise.
2. Map each approved grammar entry to a deterministic planner style and retrieval fallback path.
3. Keep unsupported generated styles out of auto-plan resolution until they are explicitly added to the grammar.
4. Add tests that verify the grammar accepts only approved styles and still preserves existing builtin retrieval behavior.
5. Keep sample/test artifacts grouped under `harness/work/tests/` unless a caller overrides `--output-root`.
6. Keep the render/demo artifact contract stable while the grammar layer is introduced.

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
