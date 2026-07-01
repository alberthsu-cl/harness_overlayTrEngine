from __future__ import annotations

from dataclasses import dataclass, asdict
from math import log10
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Any


SUPPORTED_FRAME_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg"}


@dataclass(slots=True)
class FrameScore:
    candidate_frame: str
    reference_frame: str
    mse: float
    mae: float
    psnr_db: float | None


@dataclass(slots=True)
class SimilarityScore:
    frame_count: int
    candidate_frame_count: int
    reference_frame_count: int
    width: int
    height: int
    mse: float
    mae: float
    psnr_db: float | None
    frames: list[FrameScore]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def score_frame_sequences(
    candidate: Path,
    reference: Path,
    width: int,
    height: int,
    frame_count: int | None = None,
    ffmpeg_path: str | None = None,
    require_exact_frame_count: bool = False,
) -> SimilarityScore:
    candidate_frames = discover_frames(candidate)
    reference_frames = discover_frames(reference)

    if not candidate_frames:
        raise ValueError(f"candidate contains no supported frames: {candidate}")
    if not reference_frames:
        raise ValueError(f"reference contains no supported frames: {reference}")

    candidate_frame_count = len(candidate_frames)
    reference_frame_count = len(reference_frames)

    if require_exact_frame_count:
        if frame_count is not None:
            if candidate_frame_count != frame_count:
                raise ValueError(
                    f"candidate frame count mismatch: expected {frame_count}, "
                    f"found {candidate_frame_count} in {candidate}"
                )
            if reference_frame_count != frame_count:
                raise ValueError(
                    f"reference frame count mismatch: expected {frame_count}, "
                    f"found {reference_frame_count} in {reference}"
                )
            pair_count = frame_count
        else:
            if candidate_frame_count != reference_frame_count:
                raise ValueError(
                    f"candidate/reference frame count mismatch: "
                    f"{candidate_frame_count} vs {reference_frame_count}"
                )
            pair_count = candidate_frame_count
    else:
        pair_count = min(candidate_frame_count, reference_frame_count)
        if frame_count is not None:
            pair_count = min(pair_count, frame_count)
    if pair_count <= 0:
        raise ValueError("no candidate/reference frame pairs are available to score")

    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")

    frames: list[FrameScore] = []
    total_squared_error = 0
    total_absolute_error = 0
    total_sample_count = 0

    for candidate_frame, reference_frame in zip(candidate_frames[:pair_count], reference_frames[:pair_count]):
        candidate_rgb = decode_frame_rgb(ffmpeg_executable, candidate_frame, width, height)
        reference_rgb = decode_frame_rgb(ffmpeg_executable, reference_frame, width, height)
        frame_score = score_rgb_buffers(candidate_rgb, reference_rgb, width, height)
        frames.append(
            FrameScore(
                candidate_frame=str(candidate_frame),
                reference_frame=str(reference_frame),
                mse=frame_score["mse"],
                mae=frame_score["mae"],
                psnr_db=frame_score["psnr_db"],
            )
        )
        sample_count = width * height * 3
        total_squared_error += frame_score["squared_error"]
        total_absolute_error += frame_score["absolute_error"]
        total_sample_count += sample_count

    mse = total_squared_error / total_sample_count
    mae = total_absolute_error / total_sample_count
    return SimilarityScore(
        frame_count=pair_count,
        candidate_frame_count=candidate_frame_count,
        reference_frame_count=reference_frame_count,
        width=width,
        height=height,
        mse=mse,
        mae=mae,
        psnr_db=calculate_psnr(mse),
        frames=frames,
    )


def discover_frames(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_FRAME_EXTENSIONS:
            raise ValueError(f"unsupported image format: {path}")
        return [path]

    if not path.is_dir():
        raise FileNotFoundError(f"path does not exist: {path}")

    return sorted(
        frame_path
        for frame_path in path.iterdir()
        if frame_path.is_file() and frame_path.suffix.lower() in SUPPORTED_FRAME_EXTENSIONS
    )


def decode_frame_rgb(ffmpeg_executable: str | None, frame_path: Path, width: int, height: int) -> bytes:
    if frame_path.suffix.lower() == ".bmp":
        return decode_bmp_rgb(frame_path, width, height)

    if not ffmpeg_executable:
        raise RuntimeError("ffmpeg is required for scoring non-BMP frames but was not found on PATH")

    command = [
        ffmpeg_executable,
        "-v",
        "error",
        "-i",
        str(frame_path),
        "-vf",
        f"scale={width}:{height}",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed to decode {frame_path}: {stderr}")

    expected_size = width * height * 3
    if len(completed.stdout) != expected_size:
        raise RuntimeError(
            f"decoded frame size mismatch for {frame_path}: "
            f"got {len(completed.stdout)} bytes, expected {expected_size}"
        )

    return completed.stdout


def decode_bmp_rgb(frame_path: Path, width: int, height: int) -> bytes:
    with frame_path.open("rb") as handle:
        header = handle.read(54)
        if len(header) < 54:
            raise RuntimeError(f"invalid BMP header: {frame_path}")
        signature, _, _, _, pixel_offset = struct.unpack("<2sIHHI", header[:14])
        if signature != b"BM":
            raise RuntimeError(f"invalid BMP signature: {frame_path}")

        dib_header_size = struct.unpack("<I", header[14:18])[0]
        if dib_header_size != 40:
            raise RuntimeError(f"unsupported BMP DIB header size in {frame_path}: {dib_header_size}")

        bmp_width, bmp_height, planes, bits_per_pixel, compression = struct.unpack("<iiHHI", header[18:34])
        if planes != 1 or bits_per_pixel != 24 or compression != 0:
            raise RuntimeError(f"only uncompressed 24-bit BMP frames are supported without ffmpeg: {frame_path}")
        if abs(bmp_width) != width or abs(bmp_height) != height:
            raise RuntimeError(
                f"BMP fallback cannot scale {frame_path}: "
                f"got {abs(bmp_width)}x{abs(bmp_height)}, expected {width}x{height}"
            )

        handle.seek(pixel_offset)
        row_stride = ((width * 3 + 3) // 4) * 4
        rows = [handle.read(row_stride) for _ in range(height)]

    if bmp_height > 0:
        rows.reverse()

    rgb = bytearray()
    for row in rows:
        for offset in range(0, width * 3, 3):
            blue, green, red = row[offset : offset + 3]
            rgb.extend((red, green, blue))

    return bytes(rgb)


def score_rgb_buffers(candidate_rgb: bytes, reference_rgb: bytes, width: int, height: int) -> dict[str, float | int | None]:
    if len(candidate_rgb) != len(reference_rgb):
        raise ValueError("candidate and reference buffers must have the same size")

    squared_error = 0
    absolute_error = 0
    for candidate_value, reference_value in zip(candidate_rgb, reference_rgb):
        delta = candidate_value - reference_value
        squared_error += delta * delta
        absolute_error += abs(delta)

    sample_count = width * height * 3
    mse = squared_error / sample_count
    mae = absolute_error / sample_count
    return {
        "squared_error": squared_error,
        "absolute_error": absolute_error,
        "mse": mse,
        "mae": mae,
        "psnr_db": calculate_psnr(mse),
    }


def calculate_psnr(mse: float) -> float | None:
    if mse == 0:
        return None
    return 20 * log10(255.0) - 10 * log10(mse)
