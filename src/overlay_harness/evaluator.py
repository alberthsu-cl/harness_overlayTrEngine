from __future__ import annotations

from dataclasses import dataclass, asdict
from math import log10
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
from typing import Any


SUPPORTED_FRAME_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg"}


def analyze_edge_content_policy(
    reference: Path,
    source_directories: list[Path],
    width: int,
    height: int,
    frame_start: int = 0,
    frame_end: int | None = None,
    ffmpeg_path: str | None = None,
    analysis_width: int = 160,
    analysis_height: int = 90,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Estimate source-edge continuation policy from prepared sources and reference frames.

    The classifier compares transition-frame screen-edge strips against source
    edge predictions for clamp, mirror, and repeat. It is deliberately
    conservative: a policy is selected only when repeated evidence separates it
    from the alternatives. It does not assume a transition uses displacement.
    """
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("OpenCV and NumPy are required for edge-content diagnostics") from error

    reference_frames = discover_frames(reference)
    source_frames = [
        _representative_source_frame(directory)
        for directory in source_directories
        if directory.is_dir()
    ]
    source_frames = [frame for frame in source_frames if frame is not None]
    if not reference_frames or not source_frames:
        return {
            "artifact_type": "edge_content_policy_diagnostics",
            "status": "not_applicable",
            "reason": "prepared reference frames and at least one prepared source are required",
        }
    resolved_end = len(reference_frames) - 1 if frame_end is None else min(frame_end, len(reference_frames) - 1)
    if frame_start < 0 or resolved_end <= frame_start:
        return {
            "artifact_type": "edge_content_policy_diagnostics",
            "status": "not_applicable",
            "reason": "edge-content analysis requires at least two transition frames",
        }

    resolved_width = min(width, analysis_width)
    resolved_height = min(height, analysis_height)
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    source_images = [
        _decode_rgb_array(frame, resolved_width, resolved_height, ffmpeg_executable, cv2, numpy)
        for frame in source_frames
    ]
    evidence: list[dict[str, Any]] = []
    visual_evidence: dict[tuple[int, str], tuple[Any, Any]] = {}
    policy_scores: dict[str, list[float]] = {"clamp": [], "mirror": [], "repeat": []}
    for index in range(frame_start, resolved_end + 1):
        frame = _decode_rgb_array(reference_frames[index], resolved_width, resolved_height, ffmpeg_executable, cv2, numpy)
        for edge in ("left", "right", "top", "bottom"):
            observed = _edge_strip(frame, edge, numpy)
            predictions = {
                policy: [
                    (_edge_similarity(observed, _edge_prediction(source, edge, policy, numpy), numpy), _edge_prediction(source, edge, policy, numpy))
                    for source in source_images
                ]
                for policy in policy_scores
            }
            scores = {policy: max(matches, key=lambda match: match[0])[0] for policy, matches in predictions.items()}
            for policy, score in scores.items():
                policy_scores[policy].append(score)
            ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if ordered[0][1] - ordered[1][1] >= 0.04:
                item = {
                    "frame_index": index,
                    "edge": edge,
                    "best_policy": ordered[0][0],
                    "best_similarity": ordered[0][1],
                    "margin": ordered[0][1] - ordered[1][1],
                }
                evidence.append(item)
                visual_evidence[(index, edge)] = (observed, max(predictions[ordered[0][0]], key=lambda match: match[0])[1])

    medians = {
        policy: float(numpy.median(values)) if values else 0.0
        for policy, values in policy_scores.items()
    }
    ranked = sorted(medians.items(), key=lambda item: item[1], reverse=True)
    best_policy, best_score = ranked[0]
    second_score = ranked[1][1]
    supporting = [item for item in evidence if item["best_policy"] == best_policy]
    confidence = min(1.0, max(0.0, (best_score - second_score) / 0.12)) * min(1.0, len(supporting) / 6.0)
    selected = best_policy if confidence >= 0.55 and best_score >= 0.55 else "unknown"
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for frame in output_dir.glob("edge_*.png"):
            frame.unlink()
        for item in sorted(supporting, key=lambda entry: float(entry["margin"]), reverse=True)[:12]:
            observed, predicted = visual_evidence[(item["frame_index"], item["edge"])]
            output_file = output_dir / f"edge_{item['frame_index']:04d}_{item['edge']}.png"
            _write_edge_policy_panel(observed, predicted, item, output_file, cv2, numpy)
            item["visualization"] = str(output_file)
    return {
        "artifact_type": "edge_content_policy_diagnostics",
        "status": "estimated" if selected != "unknown" else "unknown",
        "recommended_policy": selected,
        "confidence": confidence,
        "reason": (
            "source-edge continuation evidence favors the selected policy"
            if selected != "unknown"
            else "source-edge evidence does not separate clamp, mirror, and repeat reliably"
        ),
        "analysis_width": resolved_width,
        "analysis_height": resolved_height,
        "frame_range": {"start": frame_start, "end": resolved_end},
        "policy_similarity": medians,
        "evidence_count": len(supporting),
        "evidence": supporting[:12],
        "source_frames": [str(frame) for frame in source_frames],
        "output_dir": str(output_dir) if output_dir is not None else None,
    }


def _representative_source_frame(directory: Path) -> Path | None:
    frames = discover_frames(directory)
    return frames[len(frames) // 2] if frames else None


def _decode_rgb_array(
    frame: Path,
    width: int,
    height: int,
    ffmpeg_executable: str | None,
    cv2_module: Any,
    numpy_module: Any,
) -> Any:
    rgb = decode_frame_rgb(ffmpeg_executable, frame, width, height)
    return numpy_module.frombuffer(rgb, dtype=numpy_module.uint8).reshape((height, width, 3))


def _edge_strip(image: Any, edge: str, numpy_module: Any) -> Any:
    height, width = image.shape[:2]
    thickness = max(4, min(height, width) // 12)
    if edge == "left":
        return image[:, :thickness]
    if edge == "right":
        return image[:, width - thickness :]
    if edge == "top":
        return image[:thickness, :]
    return image[height - thickness :, :]


def _edge_prediction(source: Any, edge: str, policy: str, numpy_module: Any) -> Any:
    strip = _edge_strip(source, edge, numpy_module)
    if policy == "repeat":
        opposite = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}[edge]
        return _edge_strip(source, opposite, numpy_module)
    if policy == "mirror":
        axis = 1 if edge in {"left", "right"} else 0
        return numpy_module.flip(strip, axis=axis)
    if edge in {"left", "right"}:
        return numpy_module.repeat(strip[:, :1] if edge == "left" else strip[:, -1:], strip.shape[1], axis=1)
    return numpy_module.repeat(strip[:1, :] if edge == "top" else strip[-1:, :], strip.shape[0], axis=0)


def _edge_similarity(observed: Any, predicted: Any, numpy_module: Any) -> float:
    difference = numpy_module.mean(numpy_module.abs(observed.astype(numpy_module.float32) - predicted.astype(numpy_module.float32)))
    return max(0.0, 1.0 - float(difference) / 255.0)


def _write_edge_policy_panel(
    observed: Any,
    predicted: Any,
    evidence: dict[str, Any],
    output_file: Path,
    cv2_module: Any,
    numpy_module: Any,
) -> None:
    panel_height, panel_width = 90, 160
    observed_panel = cv2_module.resize(observed, (panel_width, panel_height), interpolation=cv2_module.INTER_NEAREST)
    predicted_panel = cv2_module.resize(predicted, (panel_width, panel_height), interpolation=cv2_module.INTER_NEAREST)
    panel = numpy_module.hstack((observed_panel, predicted_panel))
    cv2_module.putText(panel, "Reference edge", (6, 18), cv2_module.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2_module.putText(panel, str(evidence["best_policy"]), (panel_width + 6, 18), cv2_module.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    if not cv2_module.imwrite(str(output_file), cv2_module.cvtColor(panel, cv2_module.COLOR_RGB2BGR)):
        raise RuntimeError(f"could not write edge-content visualization: {output_file}")


def analyze_sampler_repetition(
    source_files: list[Path] | None = None,
    sampler_source: Path | None = None,
) -> dict[str, Any]:
    """Report source-level evidence for out-of-range UV repetition.

    This is intentionally advisory: the rendered pixels do not expose the
    sampler state, and ``frac`` may be used for noise rather than UV wrapping.
    """
    files = [path for path in (source_files or []) if path.is_file()]
    if sampler_source is not None and sampler_source.is_file() and sampler_source not in files:
        files.append(sampler_source)

    address_modes: dict[str, str] = {}
    address_mode_variables: dict[str, str] = {}
    uv_constructs: list[dict[str, Any]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(
            r"Address([UVW])\s*=\s*D3D11_TEXTURE_ADDRESS_([A-Z]+)", text, re.IGNORECASE
        ):
            address_modes[match.group(1)] = match.group(2).upper()
        for match in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*D3D11_TEXTURE_ADDRESS_([A-Z]+)",
            text,
            re.IGNORECASE,
        ):
            address_mode_variables[match.group(1)] = match.group(2).upper()
        for match in re.finditer(
            r"Address([UVW])\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE
        ):
            mode = address_mode_variables.get(match.group(2))
            if mode is not None:
                address_modes[match.group(1)] = mode
        if path.suffix.lower() not in {".hlsl", ".h", ".cpp", ".cxx"}:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if re.search(r"\b(?:frac|fmod|mod)\s*\(", line, re.IGNORECASE):
                uv_constructs.append(
                    {"file": str(path), "line": line_number, "text": line.strip()[:240]}
                )

    repeated_address_modes = sorted(
        mode for mode in set(address_modes.values()) if mode in {"WRAP", "MIRROR"}
    )
    if repeated_address_modes and uv_constructs:
        risk = "elevated"
        reason = "shared sampler permits repetition and shader contains modulo-like coordinate constructs"
    elif repeated_address_modes:
        risk = "possible"
        reason = "shared sampler permits repetition when transformed UVs leave the normalized range"
    elif uv_constructs:
        risk = "possible"
        reason = "shader contains modulo-like coordinate constructs; sampler mode was not found"
    else:
        risk = "not_observed"
        reason = "no sampler repetition mode or modulo-like coordinate construct was found"
    return {
        "artifact_type": "sampler_repetition_diagnostics",
        "status": "advisory",
        "risk": risk,
        "reason": reason,
        "address_modes": address_modes,
        "address_mode_variables": address_mode_variables,
        "repetition_capable_address_modes": repeated_address_modes,
        "uv_wrapping_constructs": uv_constructs[:20],
        "uv_wrapping_construct_count": len(uv_constructs),
    }


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
    total_reliable_coverage = 0.0
    for offset in range(1, len(candidate_gray)):
        candidate_flow = cv2_module.calcOpticalFlowFarneback(
            candidate_gray[offset - 1], candidate_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reference_flow = cv2_module.calcOpticalFlowFarneback(
            reference_gray[offset - 1], reference_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        candidate_backward_flow = cv2_module.calcOpticalFlowFarneback(
            candidate_gray[offset], candidate_gray[offset - 1], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reference_backward_flow = cv2_module.calcOpticalFlowFarneback(
            reference_gray[offset], reference_gray[offset - 1], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        pair = _score_flow_pair(
            candidate_flow,
            reference_flow,
            motion_threshold,
            cv2_module,
            numpy_module,
            candidate_reliable=_flow_reliability_mask(candidate_flow, candidate_backward_flow, cv2_module, numpy_module),
            reference_reliable=_flow_reliability_mask(reference_flow, reference_backward_flow, cv2_module, numpy_module),
        )
        pair["from_frame"] = frame_start + offset - 1
        pair["to_frame"] = frame_start + offset
        pairs.append(pair)
        active_pixels = int(pair["active_pixel_count"])
        total_active_pixels += active_pixels
        total_vector_error += float(pair["vector_mae"]) * active_pixels
        total_direction_agreement += float(pair["direction_agreement"]) * active_pixels
        total_region_iou += float(pair["motion_region_iou"])
        total_reliable_coverage += float(pair["reliable_motion_coverage"])

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
        "reliable_motion_coverage": total_reliable_coverage / len(pairs),
        "motion_similarity": motion_similarity,
        "motion_geometry": _summarize_motion_geometry(
            [pair["candidate_motion_geometry"] for pair in pairs], numpy_module
        ),
        "reference_motion_geometry": _summarize_motion_geometry(
            [pair["reference_motion_geometry"] for pair in pairs], numpy_module
        ),
        "angular_motion": _summarize_angular_motion(
            [pair["candidate_angular_motion"] for pair in pairs], numpy_module
        ),
        "reference_angular_motion": _summarize_angular_motion(
            [pair["reference_angular_motion"] for pair in pairs], numpy_module
        ),
        "angular_motion_phases": _summarize_angular_phases(pairs, "candidate_angular_motion", numpy_module),
        "reference_angular_motion_phases": _summarize_angular_phases(pairs, "reference_angular_motion", numpy_module),
        "regional_motion": _summarize_regional_motion(pairs, numpy_module, region_key="candidate_regions"),
        "pairs": pairs,
    }


def create_motion_visualizations(
    candidate: Path,
    reference: Path,
    output_dir: Path,
    width: int,
    height: int,
    frame_start: int,
    frame_end: int,
    ffmpeg_path: str | None = None,
    analysis_width: int = 320,
    analysis_height: int = 180,
) -> dict[str, Any]:
    """Write reference, candidate, and vector-error flow panels for review."""
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy  # type: ignore[import-not-found]
    except ImportError:
        return {"status": "skipped", "message": "OpenCV and NumPy are required for motion visualizations"}
    if frame_start < 0 or frame_end <= frame_start:
        raise ValueError("motion visualization requires a window containing at least two frames")

    candidate_frames = discover_frames(candidate)
    reference_frames = discover_frames(reference)
    if frame_end >= len(candidate_frames) or frame_end >= len(reference_frames):
        raise ValueError("motion visualization window exceeds candidate or reference frame count")
    resolved_width = min(width, analysis_width)
    resolved_height = min(height, analysis_height)
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    candidate_gray = _decode_grayscale_frames(
        candidate_frames, frame_start, frame_end, resolved_width, resolved_height, ffmpeg_executable, cv2, numpy
    )
    reference_gray = _decode_grayscale_frames(
        reference_frames, frame_start, frame_end, resolved_width, resolved_height, ffmpeg_executable, cv2, numpy
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for frame in output_dir.glob("frame_*.png"):
        frame.unlink()
    frames: list[str] = []
    for offset in range(1, len(candidate_gray)):
        candidate_flow = cv2.calcOpticalFlowFarneback(
            candidate_gray[offset - 1], candidate_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reference_flow = cv2.calcOpticalFlowFarneback(
            reference_gray[offset - 1], reference_gray[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        candidate_backward = cv2.calcOpticalFlowFarneback(
            candidate_gray[offset], candidate_gray[offset - 1], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reference_backward = cv2.calcOpticalFlowFarneback(
            reference_gray[offset], reference_gray[offset - 1], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        candidate_angular = _estimate_signed_angular_motion(
            candidate_flow, _flow_reliability_mask(candidate_flow, candidate_backward, cv2, numpy), 0.75, numpy
        )
        reference_angular = _estimate_signed_angular_motion(
            reference_flow, _flow_reliability_mask(reference_flow, reference_backward, cv2, numpy), 0.75, numpy
        )
        panel = numpy.hstack(
            (
                _flow_to_rgb(reference_flow, cv2, numpy),
                _flow_to_rgb(candidate_flow, cv2, numpy),
                _flow_error_to_rgb(candidate_flow, reference_flow, cv2, numpy),
            )
        )
        cv2.putText(panel, "Reference flow", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(panel, "Candidate flow", (resolved_width + 8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(panel, "Vector error", (resolved_width * 2 + 8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        _draw_angular_motion_overlay(panel[:, :resolved_width], reference_angular, cv2)
        _draw_angular_motion_overlay(panel[:, resolved_width : resolved_width * 2], candidate_angular, cv2)
        frame_file = output_dir / f"frame_{offset - 1:04d}.png"
        if not cv2.imwrite(str(frame_file), cv2.cvtColor(panel, cv2.COLOR_RGB2BGR)):
            raise RuntimeError(f"could not write motion visualization: {frame_file}")
        frames.append(str(frame_file))
    return {
        "status": "succeeded",
        "scorer": "opencv_farneback_dense_flow",
        "output_dir": str(output_dir),
        "frame_count": len(frames),
        "analysis_width": resolved_width,
        "analysis_height": resolved_height,
        "frames": frames,
    }


def _draw_angular_motion_overlay(panel: Any, observation: dict[str, Any], cv2_module: Any) -> None:
    if observation.get("status") != "estimated":
        cv2_module.putText(panel, "angular: indeterminate", (8, 38), cv2_module.FONT_HERSHEY_SIMPLEX, 0.38, (210, 210, 210), 1, cv2_module.LINE_AA)
        return
    pivot = observation.get("pivot")
    if not isinstance(pivot, dict):
        return
    point = (int(round(float(pivot["x"]))), int(round(float(pivot["y"]))))
    color = (70, 230, 70) if observation.get("direction") == "clockwise" else (70, 180, 255)
    cv2_module.drawMarker(panel, point, color, cv2_module.MARKER_CROSS, 12, 1, cv2_module.LINE_AA)
    label = f"angular: {observation['direction']} {float(observation['angular_velocity_degrees']):+.1f} deg"
    cv2_module.putText(panel, label, (8, 38), cv2_module.FONT_HERSHEY_SIMPLEX, 0.38, color, 1, cv2_module.LINE_AA)


def analyze_reference_motion(
    reference: Path,
    output_dir: Path,
    width: int,
    height: int,
    frame_start: int = 0,
    frame_end: int | None = None,
    ffmpeg_path: str | None = None,
    analysis_width: int = 320,
    analysis_height: int = 180,
    motion_threshold: float = 0.75,
) -> dict[str, Any]:
    """Produce confidence-aware, reference-only optical-flow evidence.

    This is deliberately descriptive: it proposes motion evidence for Codex,
    but does not classify an effect or decide source boundaries.
    """
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("OpenCV and NumPy are required for reference motion diagnostics") from error
    if frame_start < 0 or motion_threshold <= 0:
        raise ValueError("reference motion diagnostics has invalid frame range or motion threshold")

    frames = discover_frames(reference)
    resolved_end = len(frames) - 1 if frame_end is None else frame_end
    if resolved_end <= frame_start or resolved_end >= len(frames):
        raise ValueError("reference motion diagnostics requires at least two in-range frames")
    resolved_width = min(width, analysis_width)
    resolved_height = min(height, analysis_height)
    ffmpeg_executable = ffmpeg_path or shutil.which("ffmpeg")
    grayscale = _decode_grayscale_frames(
        frames, frame_start, resolved_end, resolved_width, resolved_height, ffmpeg_executable, cv2, numpy
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for frame in output_dir.glob("frame_*.png"):
        frame.unlink()
    pairs: list[dict[str, Any]] = []
    energies: list[float] = []
    for offset in range(1, len(grayscale)):
        flow = cv2.calcOpticalFlowFarneback(
            grayscale[offset - 1], grayscale[offset], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        backward = cv2.calcOpticalFlowFarneback(
            grayscale[offset], grayscale[offset - 1], None, 0.5, 3, 21, 3, 5, 1.2, 0
        )
        reliable = _flow_reliability_mask(flow, backward, cv2, numpy)
        magnitude, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1], angleInDegrees=True)
        active = numpy.logical_and(magnitude >= motion_threshold, reliable)
        regions = _describe_motion_regions(flow, magnitude, active, reliable, cv2, numpy)
        motion_geometry = _estimate_motion_geometry(flow, reliable, motion_threshold, cv2, numpy)
        angular_motion = _estimate_signed_angular_motion(flow, reliable, motion_threshold, numpy)
        active_coverage = float(numpy.count_nonzero(active) / active.size)
        reliable_coverage = float(numpy.count_nonzero(reliable) / reliable.size)
        mean_magnitude = float(magnitude[active].mean()) if numpy.count_nonzero(active) else 0.0
        energy = active_coverage * mean_magnitude
        energies.append(energy)
        from_frame = frame_start + offset - 1
        to_frame = frame_start + offset
        pairs.append(
            {
                "from_frame": from_frame,
                "to_frame": to_frame,
                "motion_energy": energy,
                "mean_active_magnitude": mean_magnitude,
                "active_motion_coverage": active_coverage,
                "reliable_motion_coverage": reliable_coverage,
                "regions": regions,
                "motion_geometry": motion_geometry,
                "angular_motion": angular_motion,
            }
        )
        panel = _reference_motion_panel(flow, active, reliable, regions, angular_motion, cv2, numpy)
        frame_file = output_dir / f"frame_{offset - 1:04d}.png"
        if not cv2.imwrite(str(frame_file), cv2.cvtColor(panel, cv2.COLOR_RGB2BGR)):
            raise RuntimeError(f"could not write reference motion diagnostic: {frame_file}")

    summary = _summarize_reference_motion(pairs, energies, motion_threshold, numpy)
    summary["motion_geometry"] = _summarize_motion_geometry(
        [pair["motion_geometry"] for pair in pairs], numpy
    )
    summary["regional_motion"] = _summarize_regional_motion(pairs, numpy)
    summary["angular_motion"] = _summarize_angular_motion(
        [pair["angular_motion"] for pair in pairs], numpy
    )
    summary["angular_motion_phases"] = _summarize_angular_phases(pairs, "angular_motion", numpy)
    return {
        "artifact_type": "reference_motion_diagnostics",
        "artifact_version": 1,
        "status": "succeeded",
        "scorer": "opencv_farneback_dense_flow",
        "reference": str(reference),
        "frame_range": {"start": frame_start, "end": resolved_end},
        "analysis_width": resolved_width,
        "analysis_height": resolved_height,
        "motion_threshold": motion_threshold,
        "output_dir": str(output_dir),
        "pairs": pairs,
        "summary": summary,
    }


def _describe_motion_regions(
    flow: Any, magnitude: Any, active: Any, reliable: Any, cv2_module: Any, numpy_module: Any
) -> list[dict[str, Any]]:
    label_count, labels, stats, centroids = cv2_module.connectedComponentsWithStats(
        active.astype(numpy_module.uint8), connectivity=8
    )
    minimum_area = max(4, int(active.size * 0.0025))
    regions: list[dict[str, Any]] = []
    for label in range(1, label_count):
        area = int(stats[label, cv2_module.CC_STAT_AREA])
        if area < minimum_area:
            continue
        mask = labels == label
        mean_dx = float(flow[..., 0][mask].mean())
        mean_dy = float(flow[..., 1][mask].mean())
        direction = math.degrees(math.atan2(mean_dy, mean_dx)) % 360.0
        x = int(stats[label, cv2_module.CC_STAT_LEFT])
        y = int(stats[label, cv2_module.CC_STAT_TOP])
        region_width = int(stats[label, cv2_module.CC_STAT_WIDTH])
        region_height = int(stats[label, cv2_module.CC_STAT_HEIGHT])
        regions.append(
            {
                "bbox": {"x": x, "y": y, "width": region_width, "height": region_height},
                "area": area,
                "area_ratio": area / int(active.size),
                "centroid": {"x": float(centroids[label][0]), "y": float(centroids[label][1])},
                "mean_dx": mean_dx,
                "mean_dy": mean_dy,
                "mean_magnitude": float(magnitude[mask].mean()),
                "direction_degrees": direction,
                "reliable_fraction": float(reliable[mask].mean()),
            }
        )
    return sorted(regions, key=lambda region: float(region["area_ratio"]), reverse=True)


def _estimate_motion_geometry(
    flow: Any,
    reliable: Any,
    motion_threshold: float,
    cv2_module: Any,
    numpy_module: Any,
) -> dict[str, Any]:
    """Estimate global and local transformation cues from one reliable flow pair."""
    magnitude = numpy_module.linalg.norm(flow, axis=2)
    valid = numpy_module.logical_and(reliable, magnitude >= motion_threshold)
    ys, xs = numpy_module.nonzero(valid)
    if len(xs) < 12:
        return {
            "status": "low_confidence",
            "reason": "insufficient reliable motion correspondences",
            "confidence": 0.0,
            "valid_coverage": float(numpy_module.count_nonzero(valid) / valid.size),
        }

    sample_step = max(1, len(xs) // 2000)
    source = numpy_module.column_stack((xs[::sample_step], ys[::sample_step])).astype(numpy_module.float32)
    vectors = flow[ys[::sample_step], xs[::sample_step]].astype(numpy_module.float32)
    destination = source + vectors
    similarity, similarity_inliers = _estimate_affine(
        cv2_module.estimateAffinePartial2D, source, destination, cv2_module
    )
    affine, affine_inliers = _estimate_affine(
        cv2_module.estimateAffine2D, source, destination, cv2_module
    )
    similarity_error = _transform_residual(similarity, source, destination, numpy_module)
    affine_error = _transform_residual(affine, source, destination, numpy_module)
    matrix = affine if affine is not None and affine_error + 0.05 < similarity_error else similarity
    model = "affine_transform" if matrix is affine else "similarity_transform"
    if matrix is None:
        model = "spatial_displacement"

    transform = _transform_properties(matrix, numpy_module)
    height, width = flow.shape[:2]
    center = numpy_module.array([width / 2.0, height / 2.0], dtype=numpy_module.float32)
    relative = source - center
    radius = numpy_module.linalg.norm(relative, axis=1)
    nonzero_radius = radius > 1.0
    residual_flow = vectors.copy()
    if matrix is not None:
        predicted = _apply_transform(matrix, source, numpy_module)
        residual_flow = destination - predicted
    radial = numpy_module.sum(residual_flow * relative, axis=1) / numpy_module.maximum(radius * radius, 1.0)
    tangential = (relative[:, 0] * residual_flow[:, 1] - relative[:, 1] * residual_flow[:, 0]) / numpy_module.maximum(radius * radius, 1.0)
    radial = radial[nonzero_radius]
    tangential = tangential[nonzero_radius]
    residual_magnitude = numpy_module.linalg.norm(residual_flow, axis=1)
    inlier_values = [similarity_inliers, affine_inliers]
    inlier_ratio = max(
        float(numpy_module.count_nonzero(value) / len(value))
        for value in inlier_values
        if value is not None and len(value)
    ) if any(value is not None and len(value) for value in inlier_values) else 0.0
    confidence = min(1.0, len(source) / 500.0) * max(0.0, min(1.0, inlier_ratio))
    return {
        "status": "estimated",
        "dominant_model": model,
        "confidence": confidence,
        "valid_coverage": float(numpy_module.count_nonzero(valid) / valid.size),
        "rotation_field": {
            "mean_degrees": transform["rotation_degrees"],
            "variation_degrees": float(numpy_module.degrees(numpy_module.std(tangential))) if len(tangential) else 0.0,
            "confidence": confidence,
        },
        "radial_scale_field": {
            "mean_ratio": 1.0 + float(numpy_module.median(radial)) if len(radial) else 1.0,
            "variation_ratio": float(numpy_module.std(radial)) if len(radial) else 0.0,
            "confidence": confidence,
        },
        "reflection_or_flip": {
            "detected": transform["reflection_detected"],
            "confidence": confidence,
        },
        "spatial_displacement": {
            "residual_energy": float(numpy_module.mean(residual_magnitude)),
            "confidence": confidence,
        },
        "scale": {
            "uniform_ratio": transform["uniform_scale"],
            "axis_ratios": transform["axis_scales"],
        },
    }


def _estimate_signed_angular_motion(
    flow: Any,
    reliable: Any,
    motion_threshold: float,
    numpy_module: Any,
) -> dict[str, Any]:
    """Fit v = translation + angular_velocity x position in image coordinates.

    Positive angular velocity is clockwise because image Y increases downward.
    The fit deliberately declines translation-like or weak/occluded flow.
    """
    magnitude = numpy_module.linalg.norm(flow, axis=2)
    active = numpy_module.logical_and(reliable, magnitude >= motion_threshold)
    height, width = flow.shape[:2]
    total_pixels = int(active.size)
    active_pixels = int(numpy_module.count_nonzero(active))
    if active_pixels < max(64, total_pixels // 200):
        return {"status": "indeterminate", "reason": "insufficient reliable angular motion", "confidence": 0.0}

    yy, xx = numpy_module.nonzero(active)
    vx = flow[..., 0][active].astype(numpy_module.float64)
    vy = flow[..., 1][active].astype(numpy_module.float64)
    x = xx.astype(numpy_module.float64) - (width - 1) * 0.5
    y = yy.astype(numpy_module.float64) - (height - 1) * 0.5
    # vx = tx - omega*y, vy = ty + omega*x.
    matrix = numpy_module.zeros((len(x) * 2, 3), dtype=numpy_module.float64)
    values = numpy_module.empty(len(x) * 2, dtype=numpy_module.float64)
    matrix[0::2, 0] = 1.0
    matrix[0::2, 2] = -y
    matrix[1::2, 1] = 1.0
    matrix[1::2, 2] = x
    values[0::2] = vx
    values[1::2] = vy
    translation_x, translation_y, omega = numpy_module.linalg.lstsq(matrix, values, rcond=None)[0]
    omega_degrees = float(omega * 180.0 / math.pi)
    if abs(omega) < 0.002:
        return {
            "status": "indeterminate",
            "reason": "angular velocity is too small relative to translation",
            "confidence": 0.0,
            "angular_velocity_degrees": omega_degrees,
        }

    pivot_x = float(-translation_y / omega + (width - 1) * 0.5)
    pivot_y = float(translation_x / omega + (height - 1) * 0.5)
    radius = numpy_module.hypot(xx - pivot_x, yy - pivot_y)
    away_from_pivot = radius >= max(8.0, min(width, height) * 0.05)
    if int(numpy_module.count_nonzero(away_from_pivot)) < 48:
        return {"status": "indeterminate", "reason": "reliable flow is concentrated at the pivot", "confidence": 0.0}

    x_pivot = xx[away_from_pivot].astype(numpy_module.float64) - pivot_x
    y_pivot = yy[away_from_pivot].astype(numpy_module.float64) - pivot_y
    predicted_x = translation_x - omega * (yy[away_from_pivot] - (height - 1) * 0.5)
    predicted_y = translation_y + omega * (xx[away_from_pivot] - (width - 1) * 0.5)
    residual = numpy_module.hypot(vx[away_from_pivot] - predicted_x, vy[away_from_pivot] - predicted_y)
    observed = numpy_module.hypot(vx[away_from_pivot], vy[away_from_pivot])
    residual_ratio = float(residual.mean() / max(float(observed.mean()), 1e-6))
    diagonal = math.hypot(width, height)
    pivot_distance = math.hypot(pivot_x - (width - 1) * 0.5, pivot_y - (height - 1) * 0.5)
    coverage = active_pixels / total_pixels
    confidence = max(0.0, min(1.0, coverage * (1.0 - min(residual_ratio, 1.0))))
    if pivot_distance > diagonal * 1.5 or residual_ratio > 0.75 or confidence < 0.12:
        return {
            "status": "indeterminate",
            "reason": "flow does not support a stable angular fit",
            "confidence": confidence,
            "angular_velocity_degrees": omega_degrees,
            "pivot": {"x": pivot_x, "y": pivot_y},
            "reliable_motion_coverage": coverage,
            "residual_ratio": residual_ratio,
        }
    direction = "clockwise" if omega_degrees > 0.0 else "counter_clockwise"
    return {
        "status": "estimated",
        "direction": direction,
        "confidence": confidence,
        "angular_velocity_degrees": omega_degrees,
        "pivot": {"x": pivot_x, "y": pivot_y},
        "reliable_motion_coverage": coverage,
        "residual_ratio": residual_ratio,
    }


def _summarize_angular_motion(observations: list[dict[str, Any]], numpy_module: Any) -> dict[str, Any]:
    valid = [item for item in observations if item.get("status") == "estimated"]
    if not valid:
        return {"status": "indeterminate", "reason": "no reliable signed angular evidence", "confidence": 0.0, "pair_count": 0}
    clockwise = [item for item in valid if item.get("direction") == "clockwise"]
    counter_clockwise = [item for item in valid if item.get("direction") == "counter_clockwise"]
    dominant = clockwise if len(clockwise) >= len(counter_clockwise) else counter_clockwise
    direction = "clockwise" if dominant is clockwise else "counter_clockwise"
    consensus = len(dominant) / len(valid)
    confidence = float(numpy_module.mean([float(item["confidence"]) for item in dominant])) * consensus
    if len(valid) < 2 or consensus < 0.65 or confidence < 0.18:
        return {
            "status": "indeterminate",
            "reason": "signed angular evidence is inconsistent across reliable pairs",
            "confidence": confidence,
            "pair_count": len(valid),
            "clockwise_pair_count": len(clockwise),
            "counter_clockwise_pair_count": len(counter_clockwise),
        }
    return {
        "status": "estimated",
        "direction": direction,
        "confidence": confidence,
        "pair_count": len(valid),
        "clockwise_pair_count": len(clockwise),
        "counter_clockwise_pair_count": len(counter_clockwise),
        "mean_angular_velocity_degrees": float(numpy_module.mean([float(item["angular_velocity_degrees"]) for item in dominant])),
    }


def _summarize_angular_phases(
    pairs: list[dict[str, Any]], observation_key: str, numpy_module: Any
) -> list[dict[str, Any]]:
    """Preserve opposite outgoing/incoming rotations instead of averaging them away."""
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_direction: str | None = None
    for pair in pairs:
        observation = pair.get(observation_key)
        if not isinstance(observation, dict) or observation.get("status") != "estimated":
            if current:
                runs.append(current)
                current = []
                current_direction = None
            continue
        direction = observation.get("direction")
        if direction not in {"clockwise", "counter_clockwise"}:
            continue
        if current and direction != current_direction:
            runs.append(current)
            current = []
        current.append(pair)
        current_direction = direction
    if current:
        runs.append(current)

    significant = [run for run in runs if len(run) >= 2]
    result: list[dict[str, Any]] = []
    for index, run in enumerate(significant):
        summary = _summarize_angular_motion([pair[observation_key] for pair in run], numpy_module)
        if len(significant) == 2:
            name = "outgoing" if index == 0 else "incoming"
        else:
            name = f"phase_{index + 1:02d}"
        result.append(
            {
                "name": name,
                "from_frame": run[0].get("from_frame"),
                "to_frame": run[-1].get("to_frame"),
                **summary,
            }
        )
    return result


def _estimate_affine(estimator: Any, source: Any, destination: Any, cv2_module: Any) -> tuple[Any | None, Any | None]:
    try:
        matrix, inliers = estimator(
            source,
            destination,
            method=cv2_module.RANSAC,
            ransacReprojThreshold=2.5,
            maxIters=300,
            confidence=0.99,
        )
    except (cv2_module.error, TypeError, ValueError):
        return None, None
    return matrix, inliers


def _apply_transform(matrix: Any, points: Any, numpy_module: Any) -> Any:
    return points @ matrix[:, :2].T + matrix[:, 2]


def _transform_residual(matrix: Any, source: Any, destination: Any, numpy_module: Any) -> float:
    if matrix is None:
        return float("inf")
    return float(numpy_module.linalg.norm(_apply_transform(matrix, source, numpy_module) - destination, axis=1).mean())


def _transform_properties(matrix: Any, numpy_module: Any) -> dict[str, Any]:
    if matrix is None:
        return {
            "rotation_degrees": 0.0,
            "uniform_scale": 1.0,
            "axis_scales": [1.0, 1.0],
            "reflection_detected": False,
        }
    linear = matrix[:, :2]
    _, singular_values, _ = numpy_module.linalg.svd(linear)
    determinant = float(numpy_module.linalg.det(linear))
    rotation_part, _, _ = numpy_module.linalg.svd(linear)
    u, _, vt = numpy_module.linalg.svd(linear)
    rotation = u @ vt
    if numpy_module.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    rotation_degrees = float(numpy_module.degrees(numpy_module.arctan2(rotation[1, 0], rotation[0, 0])))
    return {
        "rotation_degrees": rotation_degrees,
        "uniform_scale": float(numpy_module.sqrt(abs(determinant))),
        "axis_scales": [float(value) for value in singular_values],
        "reflection_detected": determinant < 0.0,
    }


def _summarize_motion_geometry(geometries: list[dict[str, Any]], numpy_module: Any) -> dict[str, Any]:
    valid = [item for item in geometries if item.get("status") == "estimated"]
    if not valid:
        return {"status": "needs_review", "reason": "no reliable transformation estimate", "confidence": 0.0}
    rotations = [float(item["rotation_field"]["mean_degrees"]) for item in valid]
    scales = [float(item["radial_scale_field"]["mean_ratio"]) for item in valid]
    residuals = [float(item["spatial_displacement"]["residual_energy"]) for item in valid]
    reflections = [bool(item["reflection_or_flip"]["detected"]) for item in valid]
    return {
        "status": "estimated",
        "dominant_model": max(
            (str(item.get("dominant_model")) for item in valid),
            key=lambda model: sum(item.get("dominant_model") == model for item in valid),
        ),
        "pair_count": len(valid),
        "confidence": float(numpy_module.mean([float(item.get("confidence", 0.0)) for item in valid])),
        "rotation_field": {
            "mean_degrees": float(numpy_module.mean(rotations)),
            "variation_degrees": float(numpy_module.std(rotations)),
        },
        "radial_scale_field": {
            "mean_ratio": float(numpy_module.mean(scales)),
            "variation_ratio": float(numpy_module.std(scales)),
        },
        "reflection_or_flip": {
            "detected": sum(reflections) > len(reflections) / 2,
            "confidence": max(sum(reflections), len(reflections) - sum(reflections)) / len(reflections),
        },
        "spatial_displacement": {
            "residual_energy": float(numpy_module.mean(residuals)),
        },
    }


def _summarize_regional_motion(
    pairs: list[dict[str, Any]], numpy_module: Any, region_key: str = "regions"
) -> dict[str, Any]:
    """Summarize continuous signed direction without quantizing to direction buckets."""
    observations: list[tuple[float, float, float]] = []
    for pair in pairs:
        for region in pair.get(region_key, []):
            area = float(region.get("area_ratio", 0.0))
            reliability = float(region.get("reliable_fraction", 0.0))
            if area < 0.01 or reliability < 0.5:
                continue
            observations.append((float(region.get("mean_dx", 0.0)), float(region.get("mean_dy", 0.0)), area * reliability))
    if not observations:
        return {"status": "needs_review", "reason": "no reliable regional motion", "confidence": 0.0}
    weights = numpy_module.array([item[2] for item in observations], dtype=numpy_module.float32)
    dx = numpy_module.array([item[0] for item in observations], dtype=numpy_module.float32)
    dy = numpy_module.array([item[1] for item in observations], dtype=numpy_module.float32)
    total_weight = float(weights.sum())
    mean_dx = float((dx * weights).sum() / max(total_weight, 1e-6))
    mean_dy = float((dy * weights).sum() / max(total_weight, 1e-6))
    magnitude = math.hypot(mean_dx, mean_dy)
    angle = math.degrees(math.atan2(mean_dy, mean_dx)) % 360.0
    horizontal = float(numpy_module.mean(numpy_module.abs(dx)))
    vertical = float(numpy_module.mean(numpy_module.abs(dy)))
    dominant_axis = "horizontal" if horizontal > vertical * 1.25 else "vertical" if vertical > horizontal * 1.25 else "mixed"
    return {
        "status": "estimated",
        "region_observation_count": len(observations),
        "mean_dx": mean_dx,
        "mean_dy": mean_dy,
        "magnitude": magnitude,
        "direction_degrees": angle,
        "dominant_axis": dominant_axis,
        "axis_confidence": abs(horizontal - vertical) / max(horizontal + vertical, 1e-6),
    }


def _reference_motion_panel(
    flow: Any,
    active: Any,
    reliable: Any,
    regions: list[dict[str, Any]],
    angular_motion: dict[str, Any],
    cv2_module: Any,
    numpy_module: Any,
) -> Any:
    flow_panel = _flow_to_rgb(flow, cv2_module, numpy_module)
    region_panel = flow_panel.copy()
    for index, region in enumerate(regions, start=1):
        bbox = region["bbox"]
        color = ((67 * index) % 255, (151 * index) % 255, (229 * index) % 255)
        cv2_module.rectangle(
            region_panel,
            (int(bbox["x"]), int(bbox["y"])),
            (int(bbox["x"] + bbox["width"]), int(bbox["y"] + bbox["height"])),
            color,
            1,
        )
        cv2_module.putText(region_panel, str(index), (int(bbox["x"]), max(12, int(bbox["y"]) + 12)), cv2_module.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2_module.LINE_AA)
    reliability_panel = cv2_module.cvtColor((reliable.astype(numpy_module.uint8) * 255), cv2_module.COLOR_GRAY2RGB)
    cv2_module.putText(flow_panel, "Reference flow", (8, 20), cv2_module.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2_module.LINE_AA)
    _draw_angular_motion_overlay(flow_panel, angular_motion, cv2_module)
    cv2_module.putText(region_panel, "Dynamic regions", (8, 20), cv2_module.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2_module.LINE_AA)
    cv2_module.putText(reliability_panel, "Flow confidence", (8, 20), cv2_module.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2_module.LINE_AA)
    return numpy_module.hstack((flow_panel, region_panel, reliability_panel))


def _summarize_reference_motion(
    pairs: list[dict[str, Any]], energies: list[float], motion_threshold: float, numpy_module: Any
) -> dict[str, Any]:
    if not pairs:
        return {"status": "needs_review", "reason": "no frame pairs were available"}
    peak_index = int(numpy_module.argmax(energies))
    peak_energy = float(energies[peak_index])
    significance = max(0.02, peak_energy * 0.1)
    significant = [index for index, energy in enumerate(energies) if energy >= significance]
    if not significant or peak_energy <= 0.0:
        return {
            "status": "needs_review",
            "reason": "no reliable motion energy exceeded the provisional threshold",
            "motion_energy_threshold": significance,
            "peak_motion_pair": pairs[peak_index],
        }
    first, last = significant[0], significant[-1]
    region_counts = [len(pair["regions"]) for pair in pairs[first : last + 1]]
    return {
        "status": "provisional",
        "reason": "motion-only evidence; verify timing and visual interpretation against the sample video",
        "motion_energy_threshold": significance,
        "provisional_active_window": {
            "start_frame": pairs[first]["from_frame"],
            "end_frame": pairs[last]["to_frame"],
        },
        "peak_motion_pair": pairs[peak_index],
        "peak_motion_frame": pairs[peak_index]["to_frame"],
        "dynamic_region_count": {
            "min": min(region_counts),
            "max": max(region_counts),
            "median": float(numpy_module.median(region_counts)),
        },
        "motion_threshold": motion_threshold,
        "topology_contract": _build_motion_topology_contract(pairs[first : last + 1]),
    }


def _build_motion_topology_contract(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract recurring directional topology without assuming a fixed region count or axis."""
    evidence: list[dict[str, Any]] = []
    for pair in pairs:
        regions = [
            region
            for region in pair.get("regions", [])
            if float(region.get("area_ratio", 0.0)) >= 0.02
            and float(region.get("reliable_fraction", 0.0)) >= 0.5
        ]
        if len(regions) < 2:
            continue
        if _has_distinct_direction_groups(regions):
            evidence.append({
                "from_frame": pair["from_frame"],
                "to_frame": pair["to_frame"],
                "reference_region_count": len(regions),
            })
    if not evidence:
        return {
            "status": "not_required",
            "reason": "reference flow has no persistent multi-direction evidence",
            "evidence_pairs": [],
        }
    return {
        "status": "required",
        "reason": "reliable reference flow contains multiple spatial regions with distinct directions",
        "minimum_concurrent_regions": 2,
        "requires_distinct_direction_groups": True,
        "evidence_pairs": evidence,
        "confidence": min(1.0, len(evidence) / max(2, len(pairs) * 0.25)),
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
    candidate_reliable: Any | None = None,
    reference_reliable: Any | None = None,
) -> dict[str, Any]:
    candidate_magnitude, _ = cv2_module.cartToPolar(candidate_flow[..., 0], candidate_flow[..., 1])
    reference_magnitude, _ = cv2_module.cartToPolar(reference_flow[..., 0], reference_flow[..., 1])
    candidate_active = candidate_magnitude >= motion_threshold
    reference_active = reference_magnitude >= motion_threshold
    if candidate_reliable is not None:
        candidate_active = numpy_module.logical_and(candidate_active, candidate_reliable)
    if reference_reliable is not None:
        reference_active = numpy_module.logical_and(reference_active, reference_reliable)
    active = numpy_module.logical_or(candidate_active, reference_active)
    active_pixel_count = int(numpy_module.count_nonzero(active))
    pixel_count = int(active.size)
    candidate_regions = _describe_motion_regions(
        candidate_flow,
        candidate_magnitude,
        candidate_active,
        candidate_reliable if candidate_reliable is not None else numpy_module.ones_like(candidate_active),
        cv2_module,
        numpy_module,
    )
    reference_regions = _describe_motion_regions(
        reference_flow,
        reference_magnitude,
        reference_active,
        reference_reliable if reference_reliable is not None else numpy_module.ones_like(reference_active),
        cv2_module,
        numpy_module,
    )
    candidate_geometry = _estimate_motion_geometry(
        candidate_flow,
        candidate_reliable if candidate_reliable is not None else numpy_module.ones_like(candidate_active),
        motion_threshold,
        cv2_module,
        numpy_module,
    )
    reference_geometry = _estimate_motion_geometry(
        reference_flow,
        reference_reliable if reference_reliable is not None else numpy_module.ones_like(reference_active),
        motion_threshold,
        cv2_module,
        numpy_module,
    )
    candidate_angular_motion = _estimate_signed_angular_motion(
        candidate_flow,
        candidate_reliable if candidate_reliable is not None else numpy_module.ones_like(candidate_active),
        motion_threshold,
        numpy_module,
    )
    reference_angular_motion = _estimate_signed_angular_motion(
        reference_flow,
        reference_reliable if reference_reliable is not None else numpy_module.ones_like(reference_active),
        motion_threshold,
        numpy_module,
    )
    if not active_pixel_count:
        return {
            "active_pixel_count": 0,
            "active_motion_coverage": 0.0,
            "reliable_motion_coverage": 0.0,
            "vector_mae": 0.0,
            "direction_agreement": 1.0,
            "motion_region_iou": 1.0,
            "reference_motion_region_count": 0,
            "candidate_motion_region_count": 0,
            "reference_regions": reference_regions,
            "candidate_regions": candidate_regions,
            "reference_has_distinct_direction_groups": False,
            "candidate_has_distinct_direction_groups": False,
            "matched_direction_region_count": 0,
            "candidate_motion_geometry": candidate_geometry,
            "reference_motion_geometry": reference_geometry,
            "candidate_angular_motion": candidate_angular_motion,
            "reference_angular_motion": reference_angular_motion,
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
        "reliable_motion_coverage": active_pixel_count / pixel_count,
        "vector_mae": vector_mae,
        "direction_agreement": direction_agreement,
        "motion_region_iou": motion_region_iou,
        "reference_motion_region_count": len(reference_regions),
        "candidate_motion_region_count": len(candidate_regions),
        "reference_regions": reference_regions,
        "candidate_regions": candidate_regions,
        "reference_has_distinct_direction_groups": _has_distinct_direction_groups(reference_regions),
        "candidate_has_distinct_direction_groups": _has_distinct_direction_groups(candidate_regions),
        "matched_direction_region_count": _match_motion_regions(reference_regions, candidate_regions),
        "candidate_motion_geometry": candidate_geometry,
        "reference_motion_geometry": reference_geometry,
        "candidate_angular_motion": candidate_angular_motion,
        "reference_angular_motion": reference_angular_motion,
    }


def _has_distinct_direction_groups(regions: list[dict[str, Any]]) -> bool:
    """Return whether reliable regions contain non-parallel motion directions."""
    reliable_regions = [
        region
        for region in regions
        if float(region.get("area_ratio", 0.0)) >= 0.02
        and float(region.get("reliable_fraction", 0.0)) >= 0.5
    ]
    for index, first_region in enumerate(reliable_regions):
        first_x, first_y = float(first_region["mean_dx"]), float(first_region["mean_dy"])
        first_length = math.hypot(first_x, first_y)
        if first_length <= 1e-6:
            continue
        for second_region in reliable_regions[index + 1 :]:
            second_x, second_y = float(second_region["mean_dx"]), float(second_region["mean_dy"])
            second_length = math.hypot(second_x, second_y)
            if second_length > 1e-6 and (first_x * second_x + first_y * second_y) / (first_length * second_length) <= 0.5:
                return True
    return False


def _match_motion_regions(
    reference_regions: list[dict[str, Any]], candidate_regions: list[dict[str, Any]]
) -> int:
    """Count reliable reference regions with a spatially and directionally matching candidate."""
    reference_regions = [
        region for region in reference_regions if float(region.get("area_ratio", 0.0)) >= 0.02
    ]
    candidate_regions = [
        region for region in candidate_regions if float(region.get("area_ratio", 0.0)) >= 0.01
    ]
    used_candidates: set[int] = set()
    matched = 0
    for reference in reference_regions:
        best_index = None
        best_score = 0.0
        for index, candidate in enumerate(candidate_regions):
            if index in used_candidates:
                continue
            overlap = _bbox_iou(reference.get("bbox"), candidate.get("bbox"))
            reference_dx = float(reference.get("mean_dx", 0.0))
            reference_dy = float(reference.get("mean_dy", 0.0))
            candidate_dx = float(candidate.get("mean_dx", 0.0))
            candidate_dy = float(candidate.get("mean_dy", 0.0))
            reference_length = math.hypot(reference_dx, reference_dy)
            candidate_length = math.hypot(candidate_dx, candidate_dy)
            direction = (
                (reference_dx * candidate_dx + reference_dy * candidate_dy)
                / (reference_length * candidate_length)
                if reference_length > 1e-6 and candidate_length > 1e-6
                else 0.0
            )
            if overlap >= 0.1 and direction >= 0.5 and overlap * direction > best_score:
                best_index = index
                best_score = overlap * direction
        if best_index is not None:
            used_candidates.add(best_index)
            matched += 1
    return matched


def _bbox_iou(first: Any, second: Any) -> float:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return 0.0
    first_left = float(first.get("x", 0.0))
    first_top = float(first.get("y", 0.0))
    first_right = first_left + float(first.get("width", 0.0))
    first_bottom = first_top + float(first.get("height", 0.0))
    second_left = float(second.get("x", 0.0))
    second_top = float(second.get("y", 0.0))
    second_right = second_left + float(second.get("width", 0.0))
    second_bottom = second_top + float(second.get("height", 0.0))
    intersection = max(0.0, min(first_right, second_right) - max(first_left, second_left)) * max(
        0.0, min(first_bottom, second_bottom) - max(first_top, second_top)
    )
    first_area = max(0.0, first_right - first_left) * max(0.0, first_bottom - first_top)
    second_area = max(0.0, second_right - second_left) * max(0.0, second_bottom - second_top)
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _flow_reliability_mask(forward_flow: Any, backward_flow: Any, cv2_module: Any, numpy_module: Any) -> Any:
    height, width = forward_flow.shape[:2]
    grid_x, grid_y = numpy_module.meshgrid(
        numpy_module.arange(width, dtype=numpy_module.float32),
        numpy_module.arange(height, dtype=numpy_module.float32),
    )
    backward_at_destination = cv2_module.remap(
        backward_flow,
        grid_x + forward_flow[..., 0],
        grid_y + forward_flow[..., 1],
        cv2_module.INTER_LINEAR,
        borderMode=cv2_module.BORDER_CONSTANT,
    )
    consistency_error = numpy_module.linalg.norm(forward_flow + backward_at_destination, axis=2)
    magnitude = numpy_module.linalg.norm(forward_flow, axis=2)
    return consistency_error <= (0.5 + 0.15 * magnitude)


def _count_motion_regions(mask: Any, cv2_module: Any, numpy_module: Any) -> int:
    labels, _, stats, _ = cv2_module.connectedComponentsWithStats(
        mask.astype(numpy_module.uint8), connectivity=8
    )
    minimum_area = max(4, int(mask.size * 0.0025))
    return sum(int(area) >= minimum_area for area in stats[1:, cv2_module.CC_STAT_AREA])


def _flow_to_rgb(flow: Any, cv2_module: Any, numpy_module: Any) -> Any:
    magnitude, angle = cv2_module.cartToPolar(flow[..., 0], flow[..., 1], angleInDegrees=True)
    maximum = max(float(numpy_module.percentile(magnitude, 95)), 1.0)
    hsv = numpy_module.zeros((*magnitude.shape, 3), dtype=numpy_module.uint8)
    hsv[..., 0] = (angle / 2).astype(numpy_module.uint8)
    hsv[..., 1] = 255
    hsv[..., 2] = numpy_module.clip(magnitude * (255.0 / maximum), 0, 255).astype(numpy_module.uint8)
    return cv2_module.cvtColor(hsv, cv2_module.COLOR_HSV2RGB)


def _flow_error_to_rgb(candidate_flow: Any, reference_flow: Any, cv2_module: Any, numpy_module: Any) -> Any:
    error = numpy_module.linalg.norm(candidate_flow - reference_flow, axis=2)
    maximum = max(float(numpy_module.percentile(error, 95)), 1.0)
    normalized = numpy_module.clip(error * (255.0 / maximum), 0, 255).astype(numpy_module.uint8)
    return cv2_module.cvtColor(cv2_module.applyColorMap(normalized, cv2_module.COLORMAP_TURBO), cv2_module.COLOR_BGR2RGB)


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

    decoded_with_opencv = _decode_frame_rgb_opencv(frame_path, width, height)
    if decoded_with_opencv is not None:
        return decoded_with_opencv

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


def _decode_frame_rgb_opencv(frame_path: Path, width: int, height: int) -> bytes | None:
    """Use in-process decoding when OpenCV is available; retain FFmpeg fallback."""
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        return None
    image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    if image.shape[1] != width or image.shape[0] != height:
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()


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
    try:
        import numpy  # type: ignore[import-not-found]
    except ImportError:
        numpy = None
    if numpy is not None:
        return _score_rgb_buffers_numpy(candidate_rgb, reference_rgb, width, height, numpy)

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


def _score_rgb_buffers_numpy(
    candidate_rgb: bytes,
    reference_rgb: bytes,
    width: int,
    height: int,
    numpy_module: Any,
) -> dict[str, float | int | None]:
    candidate = numpy_module.frombuffer(candidate_rgb, dtype=numpy_module.uint8).reshape((-1, 3)).astype(numpy_module.float64)
    reference = numpy_module.frombuffer(reference_rgb, dtype=numpy_module.uint8).reshape((-1, 3)).astype(numpy_module.float64)
    delta = candidate - reference
    squared_error = float(numpy_module.square(delta).sum())
    absolute_error = float(numpy_module.abs(delta).sum())
    candidate_luma = candidate @ numpy_module.array((0.299, 0.587, 0.114))
    reference_luma = reference @ numpy_module.array((0.299, 0.587, 0.114))
    pixel_count = width * height
    sample_count = pixel_count * 3
    mse = squared_error / sample_count
    mae = absolute_error / sample_count
    candidate_mean = float(candidate_luma.mean())
    reference_mean = float(reference_luma.mean())
    candidate_variance = float(numpy_module.mean(numpy_module.square(candidate_luma)) - candidate_mean * candidate_mean)
    reference_variance = float(numpy_module.mean(numpy_module.square(reference_luma)) - reference_mean * reference_mean)
    covariance = float(numpy_module.mean(candidate_luma * reference_luma) - candidate_mean * reference_mean)
    c1 = 6.5025
    c2 = 58.5225
    denominator = (candidate_mean * candidate_mean + reference_mean * reference_mean + c1) * (
        candidate_variance + reference_variance + c2
    )
    ssim = (
        ((2 * candidate_mean * reference_mean + c1) * (2 * covariance + c2)) / denominator
        if denominator
        else None
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
