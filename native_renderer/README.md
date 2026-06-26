# Native Renderer Shim

This project is the headless C++ renderer boundary for the Python harness.

## Purpose

It consumes the `render_request.json` written by `harness/src/main.py`, loads `OverlayTrEngine.dll`, renders a PNG sequence from two image-sequence inputs, and writes the output frames to the requested directory.

## Current assumptions

- source A and source B are image files or image-sequence folders
- output is a PNG sequence
- working color space is `SDR_709`
- effects are driven directly through `IOverlayInfoManager::SetFadeInfo`
- first milestone targets resource-free or plugin-resource-backed effects and does not require script JSON

## Build

Open `OverlayTrHarnessRenderer.vcxproj` in Visual Studio and build `Debug|x64` or `Release|x64`.

## Run

```powershell
harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe --request D:\AI_Harness\harness\work\sample_job\render\render_request.json
```

The Python harness can launch the executable automatically via:

```powershell
py -3 harness/src/main.py run --job harness/examples/render_job.sample.json --renderer harness/native_renderer/build/x64/Debug/OverlayTrHarnessRenderer.exe
```

## Known limitations

- no video decoding yet; only image files / image-sequence folders
- no batching optimizations; images are loaded per frame
- no generated-effect registration path yet
- no metrics computation in the native layer