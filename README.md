# Overlay Transition Harness

This folder contains the first implementation slice of an AI-assisted transition harness for `overlaytrengine`.

## Current scope

The scaffold added here provides:

- a stable JSON render-job contract
- repository-local config files for allowed effect categories and evaluation thresholds
- a standard-library-only Python CLI for validating jobs and preparing workspace runs
- a `prepare-video` CLI step for solid-color fixtures today and ffmpeg-based extraction later
- a `prepare-pair` CLI step for creating a dedicated A/B fixture set in one shot
- a renderer shim boundary that can invoke a native executable when provided
- a native Visual Studio console project scaffold for the headless renderer
- example `effect_spec.json` files for built-in and generated-placeholder routing

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
py -3 harness/src/main.py prepare-pair --output-root harness/examples/fixtures/blue_green --color-a blue --color-b green --width 1920 --height 1080 --frame-count 30
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

`run` currently validates the job, creates a deterministic work folder, and writes a stub report describing the missing renderer dependency.

If `--renderer` points to a built executable, `run` will invoke it with the generated `render_request.json` file.

## Source clips A and B

`source_a` and `source_b` represent the two sides of the transition:

- `source_a`: frames from the first clip, before the transition
- `source_b`: frames from the second clip, after the transition

Today, the harness expects these as image sequences or still images. The new `prepare-video` command supports two preparation paths:

- `--solid-color`: generate a synthetic frame sequence without any external dependency
- `--source-video`: extract frames from a real video file using `ffmpeg`

For the common test-fixture case, `prepare-pair` generates both sides together under one root folder:

- `source_a/`
- `source_b/`

`ffmpeg` is not installed in this environment, so only the solid-color path is runnable here today.

For quick harness testing, blue/green fixtures are a good baseline because they make transition behavior obvious without source-content noise.

The default sample render job now points to the dedicated blue/green fixture pair under `harness/examples/fixtures/blue_green/`.

When `effect.effect_spec` is provided in the render job, the native renderer now resolves the effect in one of two modes:

- `builtin`: use `runtime.fx_id` from the effect spec
- `generated`: use `runtime.registered_fx_id` when a generated effect has been compiled and registered, or `runtime.fallback_fx_id` as a temporary placeholder route

See:

- `harness/examples/effect_specs/builtin_seamless_sliding.json`
- `harness/examples/effect_specs/generated_glitch_placeholder.json`

## Next step

Add a small headless renderer executable that accepts the prepared render request written by this harness and emits an image sequence for evaluation.