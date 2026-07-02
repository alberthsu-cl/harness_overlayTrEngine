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

The headless C++ renderer shim now exists and can render frames when it is built locally and provided via `--renderer`.

The native renderer project now exists under `harness/native_renderer/`, but it must be built by the user before the Python harness can launch it.

## Folder layout

```text
harness/
  configs/
  native_renderer/
  examples/
  schemas/
  src/
  WORKLOG.md
```

## Session Handoff

Use [WORKLOG.md](/abs/path/D:/AI_Harness/harness/WORKLOG.md) as the persistent restart handoff. It records:

- the current objective
- the last completed slice
- the exact next implementation step
- resume commands and constraints

Update it whenever a meaningful implementation slice finishes so the next session can resume from the file instead of reconstructing intent from chat history.

## Conda environment

The harness Python code currently uses the standard library only, but `prepare-video --source-video` requires the `ffmpeg` executable.

Use the checked-in Conda environment file to create or restore the expected environment:

```powershell
conda env create -f harness/environment.yml
conda activate harness
```

If the environment already exists and you want to sync it to the repo definition:

```powershell
conda env update -f harness/environment.yml --prune
conda activate harness
```

Verify that `ffmpeg` is available inside the active environment:

```powershell
ffmpeg -version
Get-Command ffmpeg
```

## Commands

Run from the repository root:

```powershell
py -3 harness/src/main.py prepare-pair --output-root harness/examples/fixtures/blue_green --color-a blue --color-b green --width 1920 --height 1080 --frame-count 30
py -3 harness/src/main.py prepare-reference-transition --source-video harness/sample_glitch.mp4 --output-dir harness/work/reference_transition --fps 30 --width 1920 --height 1080 --target-frame-count 30
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.transition_hint.json --intent "generated glitch transition"
py -3 harness/src/main.py plan-job --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --job-output harness/examples/planned.render_job.json --mode builtin-seamless
py -3 harness/src/main.py validate --job harness/examples/render_job.sample.json
py -3 harness/src/main.py prepare --job harness/examples/render_job.sample.json
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json
py -3 harness/src/main.py validate --job harness/examples/render_job.effect_spec.sample.json
py -3 harness/src/main.py smoke-test
py -3 harness/src/main.py real-smoke-test
py -3 harness/src/main.py run --job harness/examples/render_job.effect_spec.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
py -3 harness/src/main.py score --candidate harness/work/<run>/artifacts --reference harness/examples/inputs/reference_transition --output harness/work/<run>/reports/similarity_score.json
py -3 harness/src/main.py smoke-test --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
py -3 harness/src/main.py real-smoke-test --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
py -3 -m unittest discover harness/tests
```

If `harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe` exists, `run`, `smoke-test`, and `real-smoke-test` will use it automatically when `--renderer` is omitted.

If you want a different build output, pass `--renderer` explicitly.

## Current Workflow

Use the current phase in this order:

1. Prepare source A and source B frames.
2. Choose a sample render job or generate one with `plan-job`.
3. Run the Python harness.
4. Inspect the output frames and generated reports.

### 1. Prepare A/B inputs

For the default fixture-based workflow, generate a paired A/B set:

```powershell
py -3 harness/src/main.py prepare-pair --output-root harness/examples/fixtures/blue_green --color-a blue --color-b green --width 1920 --height 1080 --frame-count 30
```

This creates:

- `harness/examples/fixtures/blue_green/source_a/`
- `harness/examples/fixtures/blue_green/source_b/`

If you want to prepare only one side, use `prepare-video` instead.

If you have a raw sample transition video, prepare a scoring-ready reference artifact with:

```powershell
py -3 harness/src/main.py prepare-reference-transition --source-video harness/sample_glitch.mp4 --output-dir harness/work/reference_transition --fps 30 --width 1920 --height 1080 --target-frame-count 30
```

This command:

- detects the highest-change transition window from the normalized video frames
- extracts that window
- resamples it to the requested frame count
- writes `reference_transition_manifest.json` with detected frame bounds and frame-to-progress mapping

If you later pass that prepared reference artifact to `plan-job --reference-transition`, the planner now uses the manifest `frame_count` automatically unless you also pass `--frame-count`.

### 1b. Plan a job from prepared inputs

The first analyzer layer can now generate the hint file for you from prepared inputs and simple intent text:

```powershell
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.transition_hint.json --intent "generated glitch transition"
py -3 harness/src/main.py analyze-transition --source-a harness/examples/fixtures/blue_green/source_a --source-b harness/examples/fixtures/blue_green/source_b --hint-output harness/examples/analyzed.fixture.transition_hint.json --intent "smooth sliding transition"
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.transition_hint.json --comparison-output harness/examples/analyzed.plan_comparison.json
```

Each analyzer run now writes two layers of output:

- a planner-facing `transition_hint.json`
- a richer `transition_analysis.json` artifact next to it by default

The current analysis artifact is versioned and split into:

- `sources`: the prepared input paths and optional reference transition
- `facts`: deterministic analyzer evidence, resolved style reasoning, and signals
- `planning_recommendation`: the current planner-oriented suggestion layer

If you also provide `--comparison-output`, the analyzer writes a reusable plan-comparison audit report immediately, without needing a second `plan-job` pass.

You can also choose the analysis artifact path explicitly:

```powershell
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.transition_hint.json --analysis-output harness/examples/analyzed.transition_analysis.json --intent "generated glitch transition"
```

If you do not provide intent or clip metadata, the analyzer now inspects local prepared-input signals directly from the frame folders. In the current slice that means manifest fields, sampled frame hashes, sampled file sizes, and simple sequence-level heuristics:

```powershell
py -3 harness/src/main.py analyze-transition --source-a harness/examples/fixtures/blue_green/source_a --source-b harness/examples/fixtures/blue_green/source_b --hint-output harness/examples/analyzed.signal.fixture.transition_hint.json
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.signal.real.transition_hint.json
```

This is still local and deterministic. It does not use an AI model or require any API key.

The analyzer can also read lightweight clip-derived metadata instead of only freeform intent:

```powershell
py -3 harness/src/main.py analyze-transition --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --hint-output harness/examples/analyzed.from_metadata.transition_hint.json --clip-metadata-file harness/examples/clip_metadata.sample.json
```

This command writes the same `transition_hint.json` contract consumed by `plan-job --hint-file`, and it also writes a richer analysis artifact that `plan-job --analysis-file` can consume directly.

That richer artifact now includes a versioned `planning_recommendation` block with the analyzer's suggested preset, mode, style, resolved input kind, and embedded hint snapshot.

Use `plan-job` to create a valid render job from prepared A/B inputs without hand-editing JSON:

```powershell
py -3 harness/src/main.py plan-job --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --job-output harness/examples/planned.render_job.json --mode builtin-seamless
py -3 harness/src/main.py plan-job --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --job-output harness/examples/planned.effect_spec.render_job.json --mode generated-glitch-placeholder --effect-spec-output harness/examples/planned.generated_glitch_placeholder.json
py -3 harness/src/main.py plan-job --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --reference-transition harness/work/reference_transition --job-output harness/examples/planned.from_reference.render_job.json --mode builtin-glitch
```

When `--reference-transition` points at a prepared reference artifact directory, `plan-job` reads `reference_transition_manifest.json` and adopts its `frame_count`. Use `--frame-count` only when you want to override that.

For common workflows, you can use presets instead of repeating the same paths and mode selection:

```powershell
py -3 harness/src/main.py plan-job --preset real-smoke-seamless
py -3 harness/src/main.py plan-job --preset real-smoke-glitch
py -3 harness/src/main.py plan-job --preset real-smoke-generated-glitch
py -3 harness/src/main.py plan-job --preset fixture-smoke-seamless
```

You can also let the planner choose a preset or mode from explicit inputs plus a small style hint:

```powershell
py -3 harness/src/main.py plan-job --auto --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --style seamless
py -3 harness/src/main.py plan-job --auto --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --style generated-glitch
```

The next-step contract for future analysis is a small hint file. `plan-job` can read that file and still use the same planner underneath:

```powershell
py -3 harness/src/main.py plan-job --hint-file harness/examples/transition_hint.sample.json --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --job-output harness/examples/hinted.render_job.json
py -3 harness/src/main.py plan-job --hint-file harness/examples/analyzed.transition_hint.json --source-a harness/examples/inputs/source_a_real --source-b harness/examples/inputs/source_b_real --job-output harness/examples/analyzed.render_job.json
py -3 harness/src/main.py plan-job --analysis-file harness/examples/transition_analysis.sample.json --job-output harness/examples/analyzed.from_analysis.render_job.json
py -3 harness/src/main.py plan-job --analysis-file harness/examples/transition_analysis.sample.json --recompute-plan-from-facts --job-output harness/examples/analyzed.recomputed.render_job.json
py -3 harness/src/main.py plan-job --analysis-file harness/examples/transition_analysis.sample.json --recompute-plan-from-facts --comparison-output harness/examples/analyzed.plan_comparison.json --job-output harness/examples/analyzed.recomputed.render_job.json
```

`--recompute-plan-from-facts` is useful when you want to compare the embedded recommendation against a fresh deterministic recompute from the factual analysis layer.

If you also provide `--comparison-output`, `plan-job` writes a reusable JSON audit artifact with the embedded plan, the recomputed plan, their summaries, and whether the core planning fields match.

See:

- `harness/examples/transition_hint.sample.json`
- `harness/examples/transition_analysis.sample.json`
- `harness/schemas/plan_comparison_report.schema.json`
- `harness/schemas/transition_hint.schema.json`
- `harness/schemas/transition_analysis.schema.json`
- `harness/examples/clip_metadata.sample.json`
- `harness/schemas/clip_metadata.schema.json`
- `harness/schemas/reference_transition_manifest.schema.json`

The first auto slice supports these style hints:

- `seamless`
- `smooth`
- `glitch`
- `generated-seamless`
- `generated-glitch`

`--input-kind` can be used to override auto detection when needed:

- `auto`
- `real`
- `fixture`
- `custom`

Preset values can still be overridden explicitly. For example:

```powershell
py -3 harness/src/main.py plan-job --preset real-smoke-seamless --job-output harness/examples/custom.real.render_job.json
```

Supported modes in the first planner slice are:

- `builtin-seamless`
- `builtin-glitch`
- `generated-seamless-placeholder`
- `generated-glitch-placeholder`

For generated-placeholder modes, `--effect-spec-output` is optional. If you provide it, the planner copies the matching template effect spec to that location and points the planned job at the copied file.

Supported presets in the first planner slice are:

- `real-smoke-seamless`
- `real-smoke-glitch`
- `real-smoke-generated-glitch`
- `fixture-smoke-seamless`

The older shortcut names `real-smoke` and `fixture-smoke` are still accepted as aliases.

### 2. Choose a sample job

There are two smoke-test tiers for the current phase.

The fast synthetic tier uses:

- `harness/examples/render_job.sample.json`
  baseline built-in effect render using the dedicated blue/green fixture pair
- `harness/examples/render_job.effect_spec.sample.json`
  effect-spec routing test that exercises generated-placeholder resolution through a fallback built-in effect

Treat these two files as the primary smoke-test contract:

1. `harness/examples/render_job.sample.json`
2. `harness/examples/render_job.effect_spec.sample.json`

The real-video regression tier uses:

- `harness/examples/render_job.sample.real.json`
  real-video built-in effect render using the prepared real A/B inputs
- `harness/examples/render_job.effect_spec.sample.real.json`
  real-video effect-spec routing test using the prepared real A/B inputs

Run the synthetic smoke tier with:

```powershell
py -3 harness/src/main.py smoke-test
py -3 harness/src/main.py smoke-test --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

Run the real-video smoke tier with:

```powershell
py -3 harness/src/main.py real-smoke-test
py -3 harness/src/main.py real-smoke-test --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

For harness-only automated scoring/alignment checks, run:

```powershell
py -3 -m unittest discover harness/tests
```

Without `--renderer`, each helper validates its job pair if the default renderer path is not present.
With a built default renderer or an explicit `--renderer`, each helper validates and renders its job pair, then writes a combined summary report.

Use `validate` first if you want a quick contract check before rendering:

```powershell
py -3 harness/src/main.py validate --job harness/examples/render_job.sample.json
py -3 harness/src/main.py validate --job harness/examples/render_job.effect_spec.sample.json
```

### 3. Run the renderer

Once the native renderer is built, run one of the sample jobs:

```powershell
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
py -3 harness/src/main.py run --job harness/examples/render_job.effect_spec.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

Each run creates a new work folder under `harness/work/` named after the job and timestamp.

### 4. Inspect outputs and reports

Each run writes these key artifacts inside its work folder:

- `artifacts/`
  rendered output frames such as `frame_0000.png`
- `render/render_request.json`
  the exact request passed from Python to the native renderer
- `render/renderer_result.json`
  native renderer result summary, including resolved effect information
- `reports/run_report.json`
  Python-side summary containing process output, frame-count checks, and renderer result data
- `reports/similarity_score.json`
  written automatically when the render job includes `inputs.reference_transition` and the renderer produced output frames

Use `score` when you have a rendered candidate frame sequence and a prepared reference frame sequence:

```powershell
py -3 harness/src/main.py score --candidate harness/work/<run>/artifacts --reference harness/examples/inputs/reference_transition --output harness/work/<run>/reports/similarity_score.json
```

The score report currently contains frame-level and aggregate MSE, MAE, and PSNR. If the reference is still a video file, first normalize it with `prepare-video --source-video` so candidate and reference frames use the same width, height, fps, and frame count.

If `--reference` points at a prepared reference artifact directory with `reference_transition_manifest.json`, scoring now treats that manifest as the alignment contract:

- the expected frame count must match the manifest `frame_count`
- score reports include the detected start/end frames and `frame_progress_mapping`
- prepared-reference mismatches fail instead of silently truncating

Jobs that set `inputs.reference_transition` are now also validated against that prepared reference contract before render:

- the reference path must resolve to a prepared reference artifact with `reference_transition_manifest.json`
- manifest `frame_count` must match `render.frame_count`
- the prepared reference frame files must match the manifest `frame_count`

When `inputs.reference_transition` is present in a render job, `run` now attempts that same scoring step automatically after rendering and records the result in both `reports/similarity_score.json` and `reports/run_report.json`. A scoring failure is recorded in the report but does not overwrite the render status.

If the run succeeded, you should expect:

- PNG frames in `artifacts/`
- `status: succeeded` in `reports/run_report.json`
- `status: succeeded` in `render/renderer_result.json`

If the run failed, start with:

1. `reports/run_report.json`
2. `render/renderer_result.json`
3. whether `artifacts/` contains any frames at all

For the helper command, inspect:

- `harness/work/smoke_test_.../smoke_test_report.json`
- `harness/work/real_smoke_test_.../smoke_test_report.json`

## Source clips A and B

`source_a` and `source_b` represent the two sides of the transition:

- `source_a`: frames from the first clip, before the transition
- `source_b`: frames from the second clip, after the transition

Today, the harness expects these as image sequences or still images. The new `prepare-video` command supports two preparation paths:

- `--solid-color`: generate a synthetic frame sequence without any external dependency
- `--source-video`: extract frames from a real video file using `ffmpeg`

When using `--source-video`, `--frame-count` is optional. If you provide it, extraction stops after that many frames. If you omit it, the harness extracts the full video sequence at the requested `fps`.

For the common test-fixture case, `prepare-pair` generates both sides together under one root folder:

- `source_a/`
- `source_b/`

For quick harness testing, blue/green fixtures are a good baseline because they make transition behavior obvious without source-content noise.

Both official smoke-test jobs now point to the dedicated blue/green fixture pair under `harness/examples/fixtures/blue_green/`.

When `effect.effect_spec` is provided in the render job, the native renderer now resolves the effect in one of two modes:

- `builtin`: use `runtime.fx_id` from the effect spec
- `generated`: use `runtime.registered_fx_id` when a generated effect has been compiled and registered, or `runtime.fallback_fx_id` as a temporary placeholder route

See:

- `harness/examples/effect_specs/builtin_seamless_sliding.json`
- `harness/examples/effect_specs/generated_SeamlessSliding_placeholder.json`
