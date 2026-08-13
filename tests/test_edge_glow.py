from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = HARNESS_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from overlay_harness.evaluator import analyze_edge_glow


def _require_cv2_and_numpy():
    try:
        import cv2
        import numpy
    except ImportError:
        return None, None
    return cv2, numpy


class EdgeGlowDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        cv2, numpy = _require_cv2_and_numpy()
        if cv2 is None:
            self.skipTest("OpenCV/NumPy not available")
        self.cv2 = cv2
        self.numpy = numpy
        self.width = 240
        self.height = 160
        self.columns = 6
        self.rows = 4
        self.cell_w = self.width // self.columns
        self.cell_h = self.height // self.rows

    def _make_source_b(self):
        numpy = self.numpy
        rng = numpy.random.default_rng(3)
        image = numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)
        for row in range(self.rows):
            for col in range(self.columns):
                color = rng.integers(40, 200, size=3)
                image[
                    row * self.cell_h : (row + 1) * self.cell_h,
                    col * self.cell_w : (col + 1) * self.cell_w,
                ] = color
        return image

    def _make_checkerboard_frame(self, b_image, glow: bool):
        image = b_image.copy()
        not_revealed_color = self.numpy.array([10, 10, 10], dtype=self.numpy.uint8)
        for row in range(self.rows):
            for col in range(self.columns):
                if (row + col) % 2 == 0:
                    image[
                        row * self.cell_h : (row + 1) * self.cell_h,
                        col * self.cell_w : (col + 1) * self.cell_w,
                    ] = not_revealed_color
        if glow:
            for col_edge in range(1, self.columns):
                x = col_edge * self.cell_w
                image[:, max(0, x - 4) : min(self.width, x + 4)] = 250
            for row_edge in range(1, self.rows):
                y = row_edge * self.cell_h
                image[max(0, y - 4) : min(self.height, y + 4), :] = 250
        return image

    def _write_case(self, tmp: str, glow: bool):
        root = Path(tmp)
        reference_dir = root / "reference"
        source_b_dir = root / "sources" / "source_b"
        reference_dir.mkdir(parents=True)
        source_b_dir.mkdir(parents=True)
        b_image = self._make_source_b()
        frame_image = self._make_checkerboard_frame(b_image, glow=glow)
        self.cv2.imwrite(str(source_b_dir / "frame_0000.png"), b_image)
        self.cv2.imwrite(str(reference_dir / "frame_0000.png"), frame_image)
        return reference_dir, source_b_dir

    def test_detects_a_rendered_highlight_along_the_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference_dir, source_b_dir = self._write_case(tmp, glow=True)
            result = analyze_edge_glow(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
                frame_start=0,
                frame_end=0,
                sample_offset_px=10,
                glow_threshold=10.0,
            )
            self.assertEqual(result["status"], "detected")
            self.assertGreaterEqual(result["brighter_than_both_neighbors_fraction"], 0.15)
            self.assertGreaterEqual(result["mean_brightness_delta"], 10.0)

    def test_does_not_detect_a_highlight_on_a_plain_blend_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference_dir, source_b_dir = self._write_case(tmp, glow=False)
            result = analyze_edge_glow(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
                frame_start=0,
                frame_end=0,
                sample_offset_px=10,
                glow_threshold=10.0,
            )
            self.assertEqual(result["status"], "not_detected")

    def test_not_applicable_without_source_b(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)

            result = analyze_edge_glow(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
            )
            self.assertEqual(result["status"], "not_applicable")

    def test_not_applicable_with_no_reference_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)
            source_b_dir.mkdir(parents=True)
            self.cv2.imwrite(str(source_b_dir / "frame_0000.png"), self._make_source_b())

            result = analyze_edge_glow(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
            )
            self.assertEqual(result["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
