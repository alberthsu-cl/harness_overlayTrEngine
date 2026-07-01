from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import struct
import subprocess
import time


NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
}


@dataclass(slots=True)
class PrepareVideoResult:
    mode: str
    output_dir: Path
    frame_count: int
    message: str
    manifest_file: Path


@dataclass(slots=True)
class PrepareReferenceTransitionResult:
    output_dir: Path
    frame_count: int
    message: str
    manifest_file: Path
    detected_start_frame: int
    detected_end_frame: int
    detected_frame_count: int


def prepare_solid_color_frames(
    output_dir: Path,
    color: str,
    width: int,
    height: int,
    frame_count: int,
    fps: int,
) -> PrepareVideoResult:
    rgb = parse_color(color)
    output_dir.mkdir(parents=True, exist_ok=True)

    for frame_index in range(frame_count):
        write_bmp_frame(
            output_dir / f"frame_{frame_index:04d}.bmp",
            width,
            height,
            rgb,
        )

    manifest_file = output_dir / "prepare_video_manifest.json"
    manifest = {
        "mode": "solid_color",
        "color": {
            "input": color,
            "rgb": list(rgb),
        },
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "fps": fps,
        "format": "bmp_sequence",
    }
    with manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return PrepareVideoResult(
        mode="solid_color",
        output_dir=output_dir,
        frame_count=frame_count,
        message=(
            f"generated {frame_count} solid-color BMP frames at {output_dir} "
            f"using color {color}"
        ),
        manifest_file=manifest_file,
    )


def extract_video_frames(
    source_video: Path,
    output_dir: Path,
    fps: int,
    width: int | None,
    height: int | None,
    frame_count: int | None = None,
    ffmpeg_path: str | None = None,
) -> PrepareVideoResult:
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg_executable:
        raise RuntimeError(
            "ffmpeg is required for video extraction mode but was not found on PATH"
        )
    if not source_video.exists():
        raise FileNotFoundError(f"source video does not exist: {source_video}")

    output_dir.mkdir(parents=True, exist_ok=True)
    frame_pattern = output_dir / "frame_%04d.png"

    vf_parts = [f"fps={fps}"]
    if width is not None and height is not None:
        vf_parts.append(f"scale={width}:{height}")

    command = [
        ffmpeg_executable,
        "-y",
        "-i",
        str(source_video),
        "-start_number",
        "0",
    ]
    if frame_count is not None:
        command.extend(["-frames:v", str(frame_count)])

    command.extend([
        "-vf",
        ",".join(vf_parts),
        str(frame_pattern),
    ])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {completed.returncode}: {completed.stderr.strip()}"
        )

    produced_frames = len(list(output_dir.glob("frame_*.png")))
    manifest_file = output_dir / "prepare_video_manifest.json"
    manifest = {
        "mode": "extract_video",
        "source_video": str(source_video),
        "fps": fps,
        "width": width,
        "height": height,
        "requested_frame_count": frame_count,
        "frame_count": produced_frames,
        "format": "png_sequence",
        "ffmpeg": ffmpeg_executable,
    }
    with manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return PrepareVideoResult(
        mode="extract_video",
        output_dir=output_dir,
        frame_count=produced_frames,
        message=f"extracted {produced_frames} frames from {source_video}",
        manifest_file=manifest_file,
    )


def prepare_reference_transition(
    source_video: Path,
    output_dir: Path,
    fps: int,
    width: int,
    height: int,
    target_frame_count: int,
    ffmpeg_path: str | None = None,
    analysis_width: int = 64,
    analysis_height: int = 36,
) -> PrepareReferenceTransitionResult:
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg_executable:
        raise RuntimeError(
            "ffmpeg is required for reference transition preparation but was not found on PATH"
        )
    if not source_video.exists():
        raise FileNotFoundError(f"source video does not exist: {source_video}")
    if target_frame_count < 2:
        raise ValueError("target_frame_count must be at least 2")

    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_reference_transition_output(output_dir)

    analysis_frames = _decode_analysis_frames(
        ffmpeg_executable=ffmpeg_executable,
        source_video=source_video,
        fps=fps,
        width=analysis_width,
        height=analysis_height,
    )
    detected_start_frame, detected_end_frame = detect_transition_window(
        analysis_frames=analysis_frames,
        target_frame_count=target_frame_count,
    )
    detected_frame_count = detected_end_frame - detected_start_frame + 1
    output_frame_count = min(target_frame_count, detected_frame_count)
    sampled_indexes = _resample_frame_indexes(detected_frame_count, output_frame_count)
    sampled_normalized_indexes = [
        detected_start_frame + source_index for source_index in sampled_indexes
    ]
    _extract_sampled_reference_frames(
        ffmpeg_executable=ffmpeg_executable,
        source_video=source_video,
        output_dir=output_dir,
        fps=fps,
        width=width,
        height=height,
        sampled_normalized_indexes=sampled_normalized_indexes,
    )

    manifest_file = output_dir / "reference_transition_manifest.json"
    manifest = {
        "artifact_type": "reference_transition",
        "artifact_version": 1,
        "mode": "detected_transition_window",
        "source_video": str(source_video),
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": output_frame_count,
        "requested_frame_count": target_frame_count,
        "format": "png_sequence",
        "analysis": {
            "analysis_width": analysis_width,
            "analysis_height": analysis_height,
            "normalized_clip_frame_count": len(analysis_frames),
            "detected_start_frame": detected_start_frame,
            "detected_end_frame": detected_end_frame,
            "detected_frame_count": detected_frame_count,
        },
        "frame_progress_mapping": [
            {
                "output_frame": output_index,
                "normalized_progress": (
                    output_index / (output_frame_count - 1)
                    if output_frame_count > 1
                    else 0.0
                ),
                "detected_window_source_index": source_index,
                "normalized_clip_source_frame": detected_start_frame + source_index,
            }
            for output_index, source_index in enumerate(sampled_indexes)
        ],
        "ffmpeg": ffmpeg_executable,
    }
    with manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return PrepareReferenceTransitionResult(
        output_dir=output_dir,
        frame_count=output_frame_count,
        message=(
            f"prepared {output_frame_count} normalized reference frames from "
            f"{source_video}"
        ),
        manifest_file=manifest_file,
        detected_start_frame=detected_start_frame,
        detected_end_frame=detected_end_frame,
        detected_frame_count=detected_frame_count,
    )


def parse_color(value: str) -> tuple[int, int, int]:
    normalized = value.strip().lower()
    if normalized in NAMED_COLORS:
        return NAMED_COLORS[normalized]

    if normalized.startswith("#") and len(normalized) == 7:
        return (
            int(normalized[1:3], 16),
            int(normalized[3:5], 16),
            int(normalized[5:7], 16),
        )

    rgb_parts = [part.strip() for part in normalized.split(",")]
    if len(rgb_parts) == 3:
        channel_values = tuple(int(part) for part in rgb_parts)
        if all(0 <= channel <= 255 for channel in channel_values):
            return channel_values

    raise ValueError(
        "color must be a named color, #RRGGBB, or comma-separated RGB values"
    )


def write_bmp_frame(
    file_path: Path,
    width: int,
    height: int,
    rgb: tuple[int, int, int],
) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")

    red, green, blue = rgb
    bytes_per_pixel = 3
    row_stride = ((width * bytes_per_pixel + 3) // 4) * 4
    pixel_data_size = row_stride * height
    file_size = 14 + 40 + pixel_data_size

    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        file_size,
        0,
        0,
        14 + 40,
    )
    dib_header = struct.pack(
        "<IIIHHIIIIII",
        40,
        width,
        height,
        1,
        24,
        0,
        pixel_data_size,
        2835,
        2835,
        0,
        0,
    )

    row = bytes((blue, green, red)) * width
    padding = b"\x00" * (row_stride - width * bytes_per_pixel)

    with file_path.open("wb") as handle:
        handle.write(file_header)
        handle.write(dib_header)
        for _ in range(height):
            handle.write(row)
            handle.write(padding)


def _clear_reference_transition_output(output_dir: Path) -> None:
    for pattern in ("frame_*.png", "reference_transition_manifest.json"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                _unlink_file_with_retries(path)


def _decode_analysis_frames(
    ffmpeg_executable: str,
    source_video: Path,
    fps: int,
    width: int,
    height: int,
) -> list[bytes]:
    frame_size = width * height
    command = [
        ffmpeg_executable,
        "-v",
        "error",
        "-i",
        str(source_video),
        "-vf",
        f"fps={fps},scale={width}:{height},format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed to analyze {source_video}: {stderr}")
    if len(completed.stdout) % frame_size != 0:
        raise RuntimeError("analysis frame stream size is not aligned to frame boundaries")

    frame_count = len(completed.stdout) // frame_size
    return [
        completed.stdout[index * frame_size : (index + 1) * frame_size]
        for index in range(frame_count)
    ]


def detect_transition_window(
    analysis_frames: list[bytes],
    target_frame_count: int,
) -> tuple[int, int]:
    if not analysis_frames:
        raise RuntimeError("reference transition analysis produced no frames")
    if len(analysis_frames) == 1:
        return 0, 0

    diffs = [
        _mean_absolute_frame_difference(analysis_frames[index], analysis_frames[index + 1])
        for index in range(len(analysis_frames) - 1)
    ]
    smoothed_diffs = _smooth_signal(diffs, radius=2)
    peak_index = max(range(len(smoothed_diffs)), key=smoothed_diffs.__getitem__)
    peak_value = smoothed_diffs[peak_index]

    sorted_smoothed = sorted(smoothed_diffs)
    baseline_index = max(0, int(len(sorted_smoothed) * 0.2) - 1)
    baseline_value = sorted_smoothed[baseline_index]

    transition_strength = max(peak_value - baseline_value, 1e-6)
    active_threshold = baseline_value + transition_strength * 0.18
    boundary_threshold = baseline_value + transition_strength * 0.08

    active_indexes = [
        index for index, value in enumerate(smoothed_diffs) if value >= active_threshold
    ]
    if active_indexes:
        start_diff = min(active_indexes)
        end_diff = max(active_indexes)
    else:
        start_diff = peak_index
        end_diff = peak_index

    start_diff = _expand_boundary_left(smoothed_diffs, start_diff, boundary_threshold)
    end_diff = _expand_boundary_right(smoothed_diffs, end_diff, boundary_threshold)

    weighted_indexes = [
        max(value - baseline_value, 0.0) for value in smoothed_diffs
    ]
    total_weight = sum(weighted_indexes)
    if total_weight > 0:
        lower_energy_bound = total_weight * 0.06
        upper_energy_bound = total_weight * 0.94
        cumulative_weight = 0.0
        energy_start = 0
        energy_end = len(weighted_indexes) - 1
        for index, value in enumerate(weighted_indexes):
            cumulative_weight += value
            if cumulative_weight >= lower_energy_bound:
                energy_start = index
                break
        cumulative_weight = 0.0
        for reverse_index, value in enumerate(reversed(weighted_indexes)):
            cumulative_weight += value
            if cumulative_weight >= (total_weight - upper_energy_bound):
                energy_end = len(weighted_indexes) - 1 - reverse_index
                break
        start_diff = min(start_diff, energy_start)
        end_diff = max(end_diff, energy_end)

    start_frame = start_diff
    end_frame = end_diff + 1

    minimum_window = min(len(analysis_frames), max(8, target_frame_count))
    start_frame, end_frame = _ensure_minimum_window(
        frame_count=len(analysis_frames),
        start_frame=start_frame,
        end_frame=end_frame,
        minimum_window=minimum_window,
    )

    return start_frame, end_frame


def _mean_absolute_frame_difference(frame_a: bytes, frame_b: bytes) -> float:
    total = 0
    for value_a, value_b in zip(frame_a, frame_b):
        total += abs(value_a - value_b)
    return total / len(frame_a)


def _smooth_signal(values: list[float], radius: int) -> list[float]:
    if radius <= 0 or len(values) <= 2:
        return list(values)

    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        window = values[start:end]
        smoothed.append(sum(window) / len(window))
    return smoothed


def _expand_boundary_left(values: list[float], start_index: int, threshold: float) -> int:
    index = start_index
    quiet_streak = 0
    while index > 0:
        next_value = values[index - 1]
        if next_value >= threshold:
            quiet_streak = 0
            index -= 1
            continue
        quiet_streak += 1
        if quiet_streak >= 2:
            break
        index -= 1
    return index


def _expand_boundary_right(values: list[float], end_index: int, threshold: float) -> int:
    index = end_index
    quiet_streak = 0
    while index < len(values) - 1:
        next_value = values[index + 1]
        if next_value >= threshold:
            quiet_streak = 0
            index += 1
            continue
        quiet_streak += 1
        if quiet_streak >= 2:
            break
        index += 1
    return index


def _ensure_minimum_window(
    frame_count: int,
    start_frame: int,
    end_frame: int,
    minimum_window: int,
) -> tuple[int, int]:
    current_window = end_frame - start_frame + 1
    if current_window >= minimum_window:
        return start_frame, end_frame

    deficit = minimum_window - current_window
    expand_before = deficit // 2
    expand_after = deficit - expand_before
    start_frame = max(0, start_frame - expand_before)
    end_frame = min(frame_count - 1, end_frame + expand_after)
    current_window = end_frame - start_frame + 1

    if current_window >= minimum_window:
        return start_frame, end_frame

    remaining = minimum_window - current_window
    if start_frame == 0:
        end_frame = min(frame_count - 1, end_frame + remaining)
    elif end_frame == frame_count - 1:
        start_frame = max(0, start_frame - remaining)

    return start_frame, end_frame


def _extract_sampled_reference_frames(
    ffmpeg_executable: str,
    source_video: Path,
    output_dir: Path,
    fps: int,
    width: int,
    height: int,
    sampled_normalized_indexes: list[int],
) -> None:
    if not sampled_normalized_indexes:
        raise ValueError("sampled_normalized_indexes must not be empty")

    unique_indexes = sorted(set(sampled_normalized_indexes))
    select_expression = "+".join(
        f"eq(n\\,{frame_index})" for frame_index in unique_indexes
    )
    frame_pattern = output_dir / "frame_%04d.png"
    command = [
        ffmpeg_executable,
        "-v",
        "error",
        "-y",
        "-i",
        str(source_video),
        "-vf",
        f"fps={fps},select='{select_expression}',scale={width}:{height}",
        "-vsync",
        "0",
        "-start_number",
        "0",
        str(frame_pattern),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to extract sampled reference frames: "
            f"{completed.stderr.strip()}"
        )

    produced_frames = len(list(output_dir.glob("frame_*.png")))
    if produced_frames != len(unique_indexes):
        raise RuntimeError(
            f"sampled reference extraction produced {produced_frames} frames; "
            f"expected {len(unique_indexes)}"
        )


def _resample_frame_indexes(source_frame_count: int, target_frame_count: int) -> list[int]:
    if source_frame_count <= 0:
        raise ValueError("source_frame_count must be positive")
    if target_frame_count <= 0:
        raise ValueError("target_frame_count must be positive")
    if target_frame_count == 1:
        return [0]
    if source_frame_count == 1:
        return [0]

    indexes = [
        min(
            source_frame_count - 1,
            int(round(output_index * (source_frame_count - 1) / (target_frame_count - 1))),
        )
        for output_index in range(target_frame_count)
    ]
    deduplicated_indexes: list[int] = []
    previous_index = -1
    for index in indexes:
        if index <= previous_index:
            index = min(source_frame_count - 1, previous_index + 1)
        deduplicated_indexes.append(index)
        previous_index = index
    return deduplicated_indexes


def _unlink_file_with_retries(path: Path, attempts: int = 5) -> None:
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)
