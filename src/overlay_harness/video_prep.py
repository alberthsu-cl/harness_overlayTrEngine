from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import struct
import subprocess


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
        "-vf",
        ",".join(vf_parts),
        str(frame_pattern),
    ]
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