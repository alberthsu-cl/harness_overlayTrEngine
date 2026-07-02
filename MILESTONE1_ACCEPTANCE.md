# Milestone 1 Acceptance

Milestone 1 is complete when the harness behaves as a reliable evaluator for existing effects.

## Required Commands

Run these from the repository root:

```powershell
py -3 -m unittest discover harness/tests
py -3 harness/src/main.py validate --job harness/examples/render_job.sample.json
py -3 harness/src/main.py smoke-test
py -3 harness/src/main.py real-smoke-test
py -3 harness/src/main.py score --candidate harness/work/<run>/artifacts --reference harness/examples/inputs/reference_transition --output harness/work/<run>/reports/similarity_score.json
```

## Required Artifacts

Each successful render run must produce:

- `render/render_request.json`
- `render/renderer_result.json`
- `reports/run_report.json`
- `reports/similarity_score.json` when `inputs.reference_transition` is present and rendering produces frames

## Required Run Report Fields

`reports/run_report.json` must remain a versioned evaluator summary with:

- `report_type: run_report`
- `report_version: 1`
- `status`
- `summary`
- `data.evaluation.render`
- `data.evaluation.score`
- `data.evaluation.overall_status`

## Required Score Report Fields

`reports/similarity_score.json` must remain a versioned alignment report with:

- `report_type: similarity_score`
- `report_version: 1`
- `status`
- `candidate`
- `reference`
- `alignment`
- `score`

## Validation Policy

The evaluator contract is strict for prepared references:

- `inputs.reference_transition` must resolve to a prepared reference artifact with `reference_transition_manifest.json`
- manifest `frame_count` must match `render.frame_count`
- the prepared reference frame files must match the manifest `frame_count`
- the prepared reference dimensions must match the render dimensions
- score failures propagate into the top-level `run` status

## Exit Criteria

Milestone 1 is satisfied when:

- the required commands run successfully for the checked-in sample jobs
- prepared-reference scoring is aligned and deterministic
- validator failures are explicit and early
- the run report is the stable evaluator summary entrypoint
