from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = HARNESS_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from overlay_harness.evaluator import analyze_grid_density


def _require_cv2_and_numpy():
    try:
        import cv2
        import numpy
    except ImportError:
        return None, None
    return cv2, numpy


class GridDensityDiagnosticsTests(unittest.TestCase):
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
                color = rng.integers(0, 255, size=3)
                image[
                    row * self.cell_h : (row + 1) * self.cell_h,
                    col * self.cell_w : (col + 1) * self.cell_w,
                ] = color
        return image

    def _make_checkerboard_frame(self, b_image):
        image = b_image.copy()
        not_revealed_color = self.numpy.array([10, 10, 10], dtype=self.numpy.uint8)
        for row in range(self.rows):
            for col in range(self.columns):
                if (row + col) % 2 == 0:
                    image[
                        row * self.cell_h : (row + 1) * self.cell_h,
                        col * self.cell_w : (col + 1) * self.cell_w,
                    ] = not_revealed_color
        return image

    def test_recovers_known_grid_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)
            source_b_dir.mkdir(parents=True)

            b_image = self._make_source_b()
            frame_image = self._make_checkerboard_frame(b_image)
            self.cv2.imwrite(str(source_b_dir / "frame_0000.png"), b_image)
            self.cv2.imwrite(str(reference_dir / "frame_0000.png"), frame_image)

            result = analyze_grid_density(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
                frame_start=0,
                frame_end=0,
                min_cell_px=10,
                max_cell_px=100,
            )

            self.assertEqual(result["status"], "estimated")
            self.assertAlmostEqual(result["estimated_columns"], self.columns, delta=1.0)
            self.assertAlmostEqual(result["estimated_rows"], self.rows, delta=1.0)

    def test_not_applicable_without_source_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)
            # source_b_dir intentionally not created

            result = analyze_grid_density(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
            )

            self.assertEqual(result["status"], "not_applicable")

    def test_not_applicable_with_no_reference_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)
            source_b_dir.mkdir(parents=True)
            self.cv2.imwrite(str(source_b_dir / "frame_0000.png"), self._make_source_b())
            # reference_dir intentionally left empty

            result = analyze_grid_density(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
            )

            self.assertEqual(result["status"], "not_applicable")

    def test_low_confidence_on_pure_noise(self):
        numpy = self.numpy
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_dir = root / "reference"
            source_b_dir = root / "sources" / "source_b"
            reference_dir.mkdir(parents=True)
            source_b_dir.mkdir(parents=True)

            rng = numpy.random.default_rng(11)
            b_image = rng.integers(0, 255, size=(self.height, self.width, 3), dtype=numpy.uint8)
            frame_image = rng.integers(0, 255, size=(self.height, self.width, 3), dtype=numpy.uint8)
            self.cv2.imwrite(str(source_b_dir / "frame_0000.png"), b_image)
            self.cv2.imwrite(str(reference_dir / "frame_0000.png"), frame_image)

            result = analyze_grid_density(
                reference=reference_dir,
                source_b_directory=source_b_dir,
                width=self.width,
                height=self.height,
                frame_start=0,
                frame_end=0,
                min_cell_px=10,
                max_cell_px=100,
            )

            self.assertEqual(result["status"], "low_confidence")


if __name__ == "__main__":
    unittest.main()
