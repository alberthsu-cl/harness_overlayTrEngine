# AGENTS.md

This repository is the `harness/` project in a workspace with two sibling repositories:

- `harness/`: Python orchestration, analysis, planning, rendering requests, scoring, and the headless C++ renderer shim.
- `../overlaytrengine/`: Visual Studio/C++ Direct3D target project that builds `OverlayTrEngine.dll` and `OverlayTrPlugInFx.dll`.

The workspace root is not a Git repository. Run Git commands inside the appropriate child repository.

## Current Goal

The long-term goal is an AI transition harness that can take a short sample transition video and eventually produce HLSL plus a C++ wrapper class for `overlaytrengine/OverlayTrPlugInFx`.

The practical rollout is staged:

1. Build a deterministic harness that prepares inputs, renders known effects, extracts reference frames, and scores similarity.
2. Index existing `OverlayTrPlugInFx` transitions and use retrieval before generation.
3. Generate only from a fixed effect grammar: wipe, dissolve, mask, UV shift, feathering, RGB split, and noise.
4. Add C++/HLSL code generation only after the harness can compile and visually evaluate candidates.

## Harness Project

Run Python commands from the workspace root:

```powershell
py -3 harness/src/main.py --help
py -3 harness/src/main.py validate --job harness/examples/render_job.sample.json
py -3 harness/src/main.py smoke-test
py -3 harness/src/main.py real-smoke-test
```

The Conda environment is defined by `harness/environment.yml`.

```powershell
conda env create -f harness/environment.yml
conda activate harness
```

`prepare-video --source-video` and non-BMP scoring require `ffmpeg`. The checked-in Conda environment includes `ffmpeg`.

For restart-safe continuity, keep `harness/WORKLOG.md` updated with the current objective, last completed step, and exact next implementation step.

Important harness modules:

- `src/overlay_harness/cli.py`: command routing.
- `src/overlay_harness/analyzer.py`: deterministic transition analysis from intent, metadata, and prepared frame signals.
- `src/overlay_harness/planner.py`: maps hints/analysis to render jobs and effect-spec placeholder routing.
- `src/overlay_harness/renderer.py`: writes `render_request.json` and invokes the native renderer when available.
- `src/overlay_harness/evaluator.py`: frame-sequence similarity scoring.
- `src/overlay_harness/video_prep.py`: solid-color fixture generation and ffmpeg frame extraction.
- `schemas/`: JSON contracts.
- `native_renderer/`: C++ console renderer shim.

The native renderer default path is:

```text
harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

If that executable exists, `run`, `smoke-test`, and `real-smoke-test` use it automatically when `--renderer` is omitted.

## Harness Flow

Current implemented flow:

1. Prepare source A/B frames with `prepare-video` or `prepare-pair`.
2. Generate a transition hint and richer analysis artifact with `analyze-transition`.
3. Generate a render job with `plan-job`.
4. Validate or run the job.
5. `run` creates a timestamped workspace under `harness/work/`.
6. The Python harness writes `render/render_request.json`.
7. The native renderer loads `overlaytrengine/x64/Debug/OverlayTrEngine.dll` or `overlaytrengine/x64/Release/OverlayTrEngine.dll`.
8. Rendered PNG frames go to `artifacts/`.
9. Reports are written under `reports/`.

Use `score` to compare a rendered candidate frame sequence against a prepared reference frame sequence:

```powershell
py -3 harness/src/main.py score --candidate harness/work/<run>/artifacts --reference harness/examples/inputs/reference_transition --output harness/work/<run>/reports/similarity_score.json
```

The current score report contains frame-level and aggregate MSE, MAE, and PSNR.

## OverlayTrEngine Project

`../overlaytrengine/OverlayTrEngine.sln` is a Visual Studio 17 solution. Projects include:

- `OverlayTrEngine`
- `OverlayTrPlugInFx`
- `OverlayTrTool`
- `DXGIRender`

`build_config.py` lists `OverlayTrEngine.sln` with `Release|x64` and `Debug|x64` build configurations and expects outputs such as:

- `x64/Debug/OverlayTrEngine.dll`
- `x64/Debug/OverlayTrPlugInFx.dll`
- `x64/Release/OverlayTrEngine.dll`
- `x64/Release/OverlayTrPlugInFx.dll`

`run_release.py` delegates to the `BuildScript` submodule release build script.

The native renderer shim includes headers from `overlaytrengine/CommonSrc` and `overlaytrengine/OverlayTrTool`, and links against Windows/D3D libraries.

## Target Runtime Structure

Confirmed runtime path:

- `OverlayTrEngine/PlugInFxManager.cpp` loads `OverlayTrPlugInFx.dll`.
- `OverlayTrEngine` drives transition rendering through `IOverlayInfoManager::SetFadeInfo`, `SetBuffer`, and `Render(progress)`.
- `OverlayTrPlugInFx/OverlayTrPlugInFx.cpp` maps FX IDs to concrete effect classes.
- Existing plugin effects inherit from `CFxBase` in `OverlayTrPlugInFx/FxBase.h`.
- Existing effects usually pair C++ classes with HLSL shaders compiled through Visual Studio `FxCompile` items.
- Generated shader headers under `OverlayTrEngine/Shader/`, `OverlayTrPlugInFx/Shader/`, and `DXGIRender/.../Shader/*.h` are build outputs and are ignored.

The current harness does not yet generate or patch `overlaytrengine` C++/HLSL source. Placeholder generated modes currently route through effect specs with fallback FX IDs.

## Code And Repo Hygiene

Do not commit local build outputs or generated run artifacts:

- `harness/work/`
- `harness/native_renderer/build/`
- Visual Studio `.vs/`
- `*.vcxproj.user`
- `overlaytrengine/**/x64/`
- `overlaytrengine/**/Debug/`
- `overlaytrengine/**/Release/`
- generated shader headers

Be careful with Git lock files. This workspace has previously had stale `.git/index.lock` files. Before removing a lock, check for active Git/TortoiseGit work and remove only a clearly stale lock.

Do not delete user media or generated build artifacts unless the user explicitly asks for cleanup.

## Coding Conventions

Harness Python:

- Uses standard-library-first Python.
- Keep CLI behavior explicit and JSON artifacts stable.
- Prefer repo-root-relative paths in examples and artifacts.
- Put reusable behavior in `src/overlay_harness/*.py`, not directly in `src/main.py`.

Target C++:

- Follow existing Visual Studio project structure.
- Existing target code uses Windows/COM/Direct3D conventions and `HRESULT`.
- Existing transition effects are concrete C++ classes selected by FX ID.
- Do not add open-ended generated code directly into `OverlayTrPlugInFx` until the harness can validate, render, and score candidates.

## Ask Before Guessing

Ask the user before making changes if any of these are needed and not already specified:

- The exact generated-effect naming convention or FX ID namespace.
- The final location for generated C++/HLSL source files.
- Whether to edit `FxInfo.h`, `OverlayTrPlugInFx.cpp`, project files, or all of them for registration.
- Whether a validation threshold is acceptable for a generated effect.
- Whether to remove untracked local media, build outputs, or Visual Studio state.
