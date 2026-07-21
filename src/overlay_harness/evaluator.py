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
        "scorer": "horizontal_band_fallback",
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


def score_motion(
    candidate: Path,
    reference: Path,
    width: int,
    height: int,
    frame_start: int,
    frame_end: int,
    ffmpeg_path: str | None = None,
    analysis_width: int = 320,
    analysis_height: int = 180,
) -> dict[str, Any]:
    """Score general 2D transition motion, with a dependency-free fallback."""
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy  # type: ignore[import-not-found]
    except ImportError:
        return score_horizontal_band_motion(
            candidate=candidate,
            reference=reference,
            width=width,
            height=height,
            frame_start=frame_start,
            frame_end=frame_end,
            ffmpeg_path=ffmpeg_path,
        )
    return score_optical_flow_motion(
        candidate=candidate,
        reference=reference,
        width=width,
        height=height,
        frame_start=frame_start,
        frame_end=frame_end,
        ffmpeg_path=ffmpeg_path,
        analysis_width=analysis_width,
        analysis_height=analysis_height,
        cv2_module=cv2,
        numpy_module=numpy,
    )


def score_optical_flow_motion(
    candidate: Path,
    reference: Path,
    width: int,
    height: int,
    frame_start: int,
    frame_end: int,
    ffmpeg_path: str | None = None,
    analysis_width: int = 320,
    analysis_height: int = 180,
    motion_threshold: float = 0.75,
    cv2_module: Any | None = None,
    numpy_module: Any | None = None,
) -> dict[str, Any]:
    """Compare dense 2D optical flow and dynamically derived motion regions."""
    if frame_start < 0 or frame_end <= frame_start:
        raise ValueError("motion scoring requires a window containing at least two frames")
    if analysis_width < 16 or analysis_height < 16 or motion_threshold <= 0:
        raise ValueError("motion scoring has invalid analysis parameters")
    if cv2_module is None or numpy_module is None:
        try:
            import cv2 as cv2_module  # type: ignore[import-not-found]
            import numpy as numpy_module  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("OpenCV and NumPy are required for optical-flow scoring") from error

    candidate_frames = discover_frames(candidate)
    reference_frames = discover_frames(reference)
    if frame_end >= len(candidate_frames) or frame_end >= len(reference_frames):
        raise ValueError("motion score window exceeds candidate or reference frame count")
    resolved_width = min(width, analysis_width)
    resolved_height = min(height, analysis_height)
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")

    candidate_gray = _decode_grayscale_frames(
        candidate_frames, frame_start, frame_end, resolved_width, resolved_height, ffmpeg_executable, cv2_module, numpy_module
    )
    reference_gray = _decode_grayscale_frames(
        reference_frames, frame_start, frame_end, resolved_width, resolved_height, ffmpeg_executable, cv2_module, numpy_module
    )

    pairs: list[dict[str, Any]] = []
    total_active_pixels = 0
    total_vector_error = 0.0
    total_direction_agreement = 0.0
    total_region_iou = 0.0
    for offset in range(1, len(candidate_gray)):
        candidate_flow = cv2_module.calcOpticalFlowFarneback(
            candidate_gray[offset - 1], candidate_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reference_flow = cv2_module.calcOpticalFlowFarneback(
            reference_gray[offset - 1], reference_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        pair = _score_flow_pair(candidate_flow, reference_flow, motion_threshold, cv2_module, numpy_module)
        pair["from_frame"] = frame_start + offset - 1
        pair["to_frame"] = frame_start + offset
        pairs.append(pair)
        active_pixels = int(pair["active_pixel_count"])
        total_active_pixels += active_pixels
        total_vector_error += float(pair["vector_mae"]) * active_pixels
        total_direction_agreement += float(pair["direction_agreement"]) * active_pixels
        total_region_iou += float(pair["motion_region_iou"])

    if total_active_pixels:
        vector_mae = total_vector_error / total_active_pixels
        direction_agreement = total_direction_agreement / total_active_pixels
    else:
        vector_mae = 0.0
        direction_agreement = 1.0
    region_iou = total_region_iou / len(pairs)
    motion_similarity = 1.0 / (1.0 + vector_mae / 4.0)
    return {
        "scorer": "opencv_farneback_dense_flow",
        "analysis_width": resolved_width,
        "analysis_height": resolved_height,
        "motion_threshold": motion_threshold,
        "pair_count": len(pairs),
        "flow_vector_mae": vector_mae,
        "direction_agreement": direction_agreement,
        "motion_region_iou": region_iou,
        "motion_similarity": motion_similarity,
        "pairs": pairs,
    }


def _decode_grayscale_frames(
    frames: list[Path],
    frame_start: int,
    frame_end: int,
    width: int,
    height: int,
    ffmpeg_executable: str | None,
    cv2_module: Any,
    numpy_module: Any,
) -> list[Any]:
    grayscale_frames: list[Any] = []
    for index in range(frame_start, frame_end + 1):
        rgb = decode_frame_rgb(ffmpeg_executable, frames[index], width, height)
        image = numpy_module.frombuffer(rgb, dtype=numpy_module.uint8).reshape((height, width, 3))
        grayscale_frames.append(cv2_module.cvtColor(image, cv2_module.COLOR_RGB2GRAY))
    return grayscale_frames


def _score_flow_pair(
    candidate_flow: Any,
    reference_flow: Any,
    motion_threshold: float,
    cv2_module: Any,
    numpy_module: Any,
) -> dict[str, Any]:
    candidate_magnitude, _ = cv2_module.cartToPolar(candidate_flow[..., 0], candidate_flow[..., 1])
    reference_magnitude, _ = cv2_module.cartToPolar(reference_flow[..., 0], reference_flow[..., 1])
    candidate_active = candidate_magnitude >= motion_threshold
    reference_active = reference_magnitude >= motion_threshold
    active = numpy_module.logical_or(candidate_active, reference_active)
    active_pixel_count = int(numpy_module.count_nonzero(active))
    pixel_count = int(active.size)
    if not active_pixel_count:
        return {
            "active_pixel_count": 0,
            "active_motion_coverage": 0.0,
            "vector_mae": 0.0,
            "direction_agreement": 1.0,
            "motion_region_iou": 1.0,
            "reference_motion_region_count": 0,
            "candidate_motion_region_count": 0,
        }

    vector_error = numpy_module.linalg.norm(candidate_flow - reference_flow, axis=2)
    vector_mae = float(vector_error[active].mean())
    both_active = numpy_module.logical_and(candidate_active, reference_active)
    dot_product = (candidate_flow * reference_flow).sum(axis=2)
    denominator = candidate_magnitude * reference_magnitude
    cosine = numpy_module.divide(
        dot_product,
        denominator,
        out=numpy_module.zeros_like(dot_product),
        where=denominator > 1e-6,
    )
    direction_matches = numpy_module.logical_and(both_active, cosine >= 0.5)
    direction_agreement = float(numpy_module.count_nonzero(direction_matches) / active_pixel_count)
    intersection = int(numpy_module.count_nonzero(numpy_module.logical_and(candidate_active, reference_active)))
    motion_region_iou = intersection / active_pixel_count
    return {
        "active_pixel_count": active_pixel_count,
        "active_motion_coverage": active_pixel_count / pixel_count,
        "vector_mae": vector_mae,
        "direction_agreement": direction_agreement,
        "motion_region_iou": motion_region_iou,
        "reference_motion_region_count": _count_motion_regions(reference_active, cv2_module, numpy_module),
        "candidate_motion_region_count": _count_motion_regions(candidate_active, cv2_module, numpy_module),
    }


def _count_motion_regions(mask: Any, cv2_module: Any, numpy_module: Any) -> int:
    labels, _, stats, _ = cv2_module.connectedComponentsWithStats(
        mask.astype(numpy_module.uint8), connectivity=8
    )
    minimum_area = max(4, int(mask.size * 0.0025))
    return sum(int(area) >= minimum_area for area in stats[1:, cv2_module.CC_STAT_AREA])


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
