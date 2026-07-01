from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4


HARNESS_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = HARNESS_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from overlay_harness.cli import _build_similarity_report
from overlay_harness.evaluator import score_frame_sequences
from overlay_harness.video_prep import write_bmp_frame


class ScoringAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        work_root = HARNESS_ROOT / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        self.root = work_root / f"test_scoring_alignment_{uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=False)
        self.width = 2
        self.height = 2

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_prepared_reference_report_includes_manifest_alignment(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        report = _build_similarity_report(
            repo_root=HARNESS_ROOT.parent,
            candidate=candidate_dir,
            reference=reference_dir,
            width=self.width,
            height=self.height,
            frame_count=None,
            output=self.root / "similarity_report.json",
        )

        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["alignment"]["mode"], "prepared_reference_manifest")
        self.assertTrue(report["alignment"]["strict_frame_count"])
        self.assertEqual(report["alignment"]["expected_frame_count"], 3)
        self.assertEqual(report["alignment"]["reference_manifest"]["analysis"]["detected_start_frame"], 12)
        self.assertEqual(report["alignment"]["reference_manifest"]["analysis"]["detected_end_frame"], 14)
        self.assertEqual(len(report["alignment"]["reference_manifest"]["frame_progress_mapping"]), 3)
        self.assertEqual(report["score"]["frame_count"], 3)
        self.assertEqual(report["score"]["candidate_frame_count"], 3)
        self.assertEqual(report["score"]["reference_frame_count"], 3)
        self.assertEqual(report["score"]["mse"], 0.0)
        self.assertEqual(report["score"]["mae"], 0.0)

    def test_prepared_reference_manifest_count_mismatch_fails(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        with self.assertRaisesRegex(ValueError, "prepared reference frame_count mismatch"):
            _build_similarity_report(
                repo_root=HARNESS_ROOT.parent,
                candidate=candidate_dir,
                reference=reference_dir,
                width=self.width,
                height=self.height,
                frame_count=2,
                output=self.root / "should_not_exist.json",
            )

    def test_prepared_reference_candidate_count_mismatch_fails(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(0, 0, 0), (64, 64, 64)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        with self.assertRaisesRegex(ValueError, "candidate frame count mismatch"):
            _build_similarity_report(
                repo_root=HARNESS_ROOT.parent,
                candidate=candidate_dir,
                reference=reference_dir,
                width=self.width,
                height=self.height,
                frame_count=None,
                output=self.root / "should_not_exist.json",
            )

    def test_non_prepared_reference_uses_frame_sequence_order(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(0, 0, 0), (64, 64, 64)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])

        report = _build_similarity_report(
            repo_root=HARNESS_ROOT.parent,
            candidate=candidate_dir,
            reference=reference_dir,
            width=self.width,
            height=self.height,
            frame_count=None,
            output=self.root / "non_prepared_report.json",
        )

        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["alignment"]["mode"], "frame_sequence_order")
        self.assertFalse(report["alignment"]["strict_frame_count"])
        self.assertIsNone(report["alignment"]["expected_frame_count"])
        self.assertEqual(report["score"]["frame_count"], 2)
        self.assertNotIn("reference_manifest", report["alignment"])

    def test_empty_candidate_fails(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        self._write_bmp_sequence(reference_dir, [(0, 0, 0)])

        with self.assertRaisesRegex(ValueError, "candidate contains no supported frames"):
            score_frame_sequences(
                candidate=candidate_dir,
                reference=reference_dir,
                width=self.width,
                height=self.height,
            )

    def _write_bmp_sequence(self, output_dir: Path, colors: list[tuple[int, int, int]]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for frame_index, color in enumerate(colors):
            write_bmp_frame(output_dir / f"frame_{frame_index:04d}.bmp", self.width, self.height, color)

    def _write_reference_manifest(self, output_dir: Path, frame_count: int) -> None:
        manifest = {
            "artifact_type": "reference_transition",
            "artifact_version": 1,
            "mode": "detected_transition_window",
            "source_video": "D:/AI_Harness/harness/sample_glitch.mp4",
            "fps": 30,
            "width": self.width,
            "height": self.height,
            "frame_count": frame_count,
            "requested_frame_count": 5,
            "format": "bmp_sequence",
            "analysis": {
                "analysis_width": 2,
                "analysis_height": 2,
                "normalized_clip_frame_count": 20,
                "detected_start_frame": 12,
                "detected_end_frame": 14,
                "detected_frame_count": frame_count,
            },
            "frame_progress_mapping": [
                {
                    "output_frame": output_index,
                    "normalized_progress": (output_index / (frame_count - 1)) if frame_count > 1 else 0.0,
                    "detected_window_source_index": output_index,
                    "normalized_clip_source_frame": 12 + output_index,
                }
                for output_index in range(frame_count)
            ],
            "ffmpeg": "ffmpeg",
        }
        with (output_dir / "reference_transition_manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")


if __name__ == "__main__":
    unittest.main()
