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
    ssim: float | None


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
    ssim: float | None
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
                ssim=frame_score["ssim"],
            )
        )
        sample_count = width * height * 3
        total_squared_error += frame_score["squared_error"]
        total_absolute_error += frame_score["absolute_error"]
        total_sample_count += sample_count

    mse = total_squared_error / total_sample_count
    mae = total_absolute_error / total_sample_count
    ssim = sum(frame.ssim for frame in frames if frame.ssim is not None) / len(frames) if frames else None
    return SimilarityScore(
        frame_count=pair_count,
        candidate_frame_count=candidate_frame_count,
        reference_frame_count=reference_frame_count,
        width=width,
        height=height,
        mse=mse,
        mae=mae,
        psnr_db=calculate_psnr(mse),
        ssim=ssim,
        frames=frames,
    )


def score_horizontal_band_motion(
    candidate: Path,
    reference: Path,
    width: int,
    height: int,
    frame_start: int,
    frame_end: int,
    ffmpeg_path: str | None = None,
    analysis_width: int = 160,
    analysis_height: int = 90,
    band_count: int = 4,
    max_shift: int = 12,
) -> dict[str, Any]:
    """Compare local horizontal motion without external vision dependencies.

    Each vertical band uses exhaustive low-resolution matching between adjacent
    luma frames. The output measures whether the candidate's horizontal shift
    follows the reference's direction and magnitude during the transition.
    """
    if frame_start < 0 or frame_end <= frame_start:
        raise ValueError("motion scoring requires a window containing at least two frames")
    if analysis_width < 8 or analysis_height < band_count or max_shift < 1:
        raise ValueError("motion scoring has invalid analysis dimensions")

    candidate_frames = discover_frames(candidate)
    reference_frames = discover_frames(reference)
    if frame_end >= len(candidate_frames) or frame_end >= len(reference_frames):
        raise ValueError("motion score window exceeds candidate or reference frame count")
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    resolved_width = min(width, analysis_width)
    resolved_height = min(height, analysis_height)
    resolved_max_shift = min(max_shift, max(1, (resolved_width - 4) // 2))

    candidate_luma = [
        _rgb_to_luma_buffer(
            decode_frame_rgb(ffmpeg_executable, candidate_frames[index], resolved_width, resolved_height)
        )
        for index in range(frame_start, frame_end + 1)
    ]
    reference_luma = [
        _rgb_to_luma_buffer(
            decode_frame_rgb(ffmpeg_executable, reference_frames[index], resolved_width, resolved_height)
        )
        for index in range(frame_start, frame_end + 1)
    ]

    pairs: list[dict[str, Any]] = []
    total_shift_error = 0.0
    total_direction_matches = 0
    total_band_pairs = 0
    for offset in range(1, len(candidate_luma)):
        candidate_shifts = _estimate_band_shifts(
            candidate_luma[offset - 1], candidate_luma[offset], resolved_width, resolved_height, band_count, resolved_max_shift
        )
        reference_shifts = _estimate_band_shifts(
            reference_luma[offset - 1], reference_luma[offset], resolved_width, resolved_height, band_count, resolved_max_shift
        )
        shift_errors = [abs(candidate_shift - reference_shift) for candidate_shift, reference_shift in zip(candidate_shifts, reference_shifts)]
        direction_matches = [
            _motion_direction(candidate_shift) == _motion_direction(reference_shift)
            for candidate_shift, reference_shift in zip(candidate_shifts, reference_shifts)
        ]
        total_shift_error += sum(shift_errors)
        total_direction_matches += sum(direction_matches)
        total_band_pairs += len(shift_errors)
        pairs.append(
            {
                "from_frame": frame_start + offset - 1,
                "to_frame": frame_start + offset,
                "candidate_horizontal_shifts": candidate_shifts,
                "reference_horizontal_shifts": reference_shifts,
                "mean_shift_error": sum(shift_errors) / len(shift_errors),
                "direction_agreement": sum(direction_matches) / len(direction_matches),
            }
        )

    shift_mae = total_shift_error / total_band_pairs
    direction_agreement = total_direction_matches / total_band_pairs
    return {
        "analysis_width": resolved_width,
        "analysis_height": resolved_height,
        "band_count": band_count,
        "max_shift": resolved_max_shift,
        "pair_count": len(pairs),
        "horizontal_shift_mae": shift_mae,
        "direction_agreement": direction_agreement,
        "motion_similarity": max(0.0, 1.0 - shift_mae / (2.0 * resolved_max_shift)),
        "pairs": pairs,
    }


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


def _rgb_to_luma_buffer(rgb: bytes) -> list[float]:
    return [
        _rgb_to_luma(rgb[index], rgb[index + 1], rgb[index + 2])
        for index in range(0, len(rgb), 3)
    ]


def _estimate_band_shifts(
    previous: list[float],
    current: list[float],
    width: int,
    height: int,
    band_count: int,
    max_shift: int,
) -> list[int]:
    shifts: list[int] = []
    for band_index in range(band_count):
        y_start = band_index * height // band_count
        y_end = (band_index + 1) * height // band_count
        best_shift = 0
        best_error: float | None = None
        for shift in range(-max_shift, max_shift + 1):
            error = 0.0
            sample_count = 0
            x_start = max(0, -shift)
            x_end = min(width, width - shift)
            for y in range(y_start, y_end, 2):
                row_offset = y * width
                for x in range(x_start, x_end, 2):
                    error += abs(previous[row_offset + x] - current[row_offset + x + shift])
                    sample_count += 1
            mean_error = error / sample_count
            if best_error is None or mean_error < best_error:
                best_shift = shift
                best_error = mean_error
        shifts.append(best_shift)
    return shifts


def _motion_direction(shift: int) -> int:
    if shift > 0:
        return 1
    if shift < 0:
        return -1
    return 0


def score_rgb_buffers(candidate_rgb: bytes, reference_rgb: bytes, width: int, height: int) -> dict[str, float | int | None]:
    if len(candidate_rgb) != len(reference_rgb):
        raise ValueError("candidate and reference buffers must have the same size")

    squared_error = 0
    absolute_error = 0
    candidate_luma_sum = 0.0
    reference_luma_sum = 0.0
    candidate_luma_sq_sum = 0.0
    reference_luma_sq_sum = 0.0
    product_sum = 0.0
    pixel_count = width * height
    for candidate_value, reference_value in zip(candidate_rgb, reference_rgb):
        delta = candidate_value - reference_value
        squared_error += delta * delta
        absolute_error += abs(delta)

    for index in range(0, len(candidate_rgb), 3):
        candidate_luma = _rgb_to_luma(candidate_rgb[index], candidate_rgb[index + 1], candidate_rgb[index + 2])
        reference_luma = _rgb_to_luma(reference_rgb[index], reference_rgb[index + 1], reference_rgb[index + 2])
        candidate_luma_sum += candidate_luma
        reference_luma_sum += reference_luma
        candidate_luma_sq_sum += candidate_luma * candidate_luma
        reference_luma_sq_sum += reference_luma * reference_luma
        product_sum += candidate_luma * reference_luma

    sample_count = width * height * 3
    mse = squared_error / sample_count
    mae = absolute_error / sample_count
    ssim = calculate_ssim(
        candidate_luma_sum,
        reference_luma_sum,
        candidate_luma_sq_sum,
        reference_luma_sq_sum,
        product_sum,
        pixel_count,
    )
    return {
        "squared_error": squared_error,
        "absolute_error": absolute_error,
        "mse": mse,
        "mae": mae,
        "psnr_db": calculate_psnr(mse),
        "ssim": ssim,
    }


def calculate_psnr(mse: float) -> float | None:
    if mse == 0:
        return None
    return 20 * log10(255.0) - 10 * log10(mse)


def _rgb_to_luma(red: int, green: int, blue: int) -> float:
    return 0.299 * red + 0.587 * green + 0.114 * blue


def calculate_ssim(
    candidate_luma_sum: float,
    reference_luma_sum: float,
    candidate_luma_sq_sum: float,
    reference_luma_sq_sum: float,
    product_sum: float,
    pixel_count: int,
) -> float | None:
    if pixel_count <= 0:
        return None

    mu_x = candidate_luma_sum / pixel_count
    mu_y = reference_luma_sum / pixel_count
    sigma_x_sq = (candidate_luma_sq_sum / pixel_count) - (mu_x * mu_x)
    sigma_y_sq = (reference_luma_sq_sum / pixel_count) - (mu_y * mu_y)
    sigma_xy = (product_sum / pixel_count) - (mu_x * mu_y)

    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    denominator = (mu_x * mu_x + mu_y * mu_y + c1) * (sigma_x_sq + sigma_y_sq + c2)
    if denominator == 0:
        return None
    numerator = (2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)
    return numerator / denominator
