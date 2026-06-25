# Overlay Transition Harness

This folder contains the first implementation slice of an AI-assisted transition harness for `overlaytrengine`.

## Current scope

The scaffold added here provides:

- a stable JSON render-job contract
- repository-local config files for allowed effect categories and evaluation thresholds
- a standard-library-only Python CLI for validating jobs and preparing workspace runs
- a renderer stub that records the future CLI invocation boundary

It does not yet render frames. That dependency belongs to the next implementation slice: a headless C++ renderer shim around `OverlayTrEngine`.

## Folder layout

```text
harness/
  configs/
  examples/
  schemas/
  src/
```

## Commands

Run from the repository root:

```powershell
py -3 harness/src/main.py validate --job harness/examples/render_job.sample.json
py -3 harness/src/main.py prepare --job harness/examples/render_job.sample.json
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json
```

`run` currently validates the job, creates a deterministic work folder, and writes a stub report describing the missing renderer dependency.

## Next step

Add a small headless renderer executable that accepts the prepared render request written by this harness and emits an image sequence for evaluation.