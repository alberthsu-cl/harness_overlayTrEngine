# Overlay Transition Harness

This folder contains the first implementation slice of an AI-assisted transition harness for `overlaytrengine`.

## Current scope

The scaffold added here provides:

- a stable JSON render-job contract
- repository-local config files for allowed effect categories and evaluation thresholds
- a standard-library-only Python CLI for validating jobs and preparing workspace runs
- a renderer shim boundary that can invoke a native executable when provided
- a native Visual Studio console project scaffold for the headless renderer

It does not yet render frames. That dependency belongs to the next implementation slice: a headless C++ renderer shim around `OverlayTrEngine`.

The native renderer project now exists under `harness/native_renderer/`, but it must be built by the user before the Python harness can launch it.

## Folder layout

```text
harness/
  configs/
  native_renderer/
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
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

`run` currently validates the job, creates a deterministic work folder, and writes a stub report describing the missing renderer dependency.

If `--renderer` points to a built executable, `run` will invoke it with the generated `render_request.json` file.

## Next step

Add a small headless renderer executable that accepts the prepared render request written by this harness and emits an image sequence for evaluation.