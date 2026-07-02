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
from overlay_harness.cli import _build_run_evaluation_summary
from overlay_harness.cli import _resolve_run_report_status
from overlay_harness.cli import _resolve_run_report_summary
from overlay_harness.evaluator import score_frame_sequences
from overlay_harness.models import EffectSpec, InputSpec, RenderJob, RenderSettings
from overlay_harness.report import HarnessReport
from overlay_harness.validator import validate_job
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

    def test_run_evaluation_summary_reports_score_alignment(self) -> None:
        class Invocation:
            status = "succeeded"
            exit_code = 0
            produced_frame_count = 3
            expected_frame_count = 3
            message = "renderer completed successfully"

        similarity_report = {
            "status": "succeeded",
            "alignment": {"mode": "prepared_reference_manifest"},
            "score": {"frame_count": 3},
        }

        summary = _build_run_evaluation_summary(Invocation(), similarity_report, self.root / "similarity_score.json")

        self.assertEqual(summary["overall_status"], "succeeded_with_score")
        self.assertEqual(summary["render"]["status"], "succeeded")
        self.assertEqual(summary["score"]["status"], "succeeded")
        self.assertEqual(summary["score"]["alignment_mode"], "prepared_reference_manifest")
        self.assertEqual(summary["score"]["frame_count"], 3)
        self.assertEqual(summary["score"]["report_file"], str(self.root / "similarity_score.json"))

    def test_run_evaluation_summary_handles_missing_score(self) -> None:
        class Invocation:
            status = "blocked"
            exit_code = None
            produced_frame_count = 0
            expected_frame_count = 3
            message = "renderer executable is not available yet; render request recorded only"

        summary = _build_run_evaluation_summary(Invocation(), None, None)

        self.assertEqual(summary["overall_status"], "blocked")
        self.assertEqual(summary["render"]["status"], "blocked")
        self.assertIsNone(summary["score"]["status"])
        self.assertIsNone(summary["score"]["alignment_mode"])
        self.assertIsNone(summary["score"]["frame_count"])
        self.assertIsNone(summary["score"]["report_file"])

    def test_run_report_is_versioned(self) -> None:
        report = HarnessReport(
            status="succeeded",
            summary="renderer completed successfully",
            data={"evaluation": {"overall_status": "succeeded_with_score"}},
        )
        report_path = self.root / "run_report.json"
        report.write(report_path)

        with report_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["report_type"], "run_report")
        self.assertEqual(payload["report_version"], 1)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["summary"], "renderer completed successfully")
        self.assertEqual(payload["data"]["evaluation"]["overall_status"], "succeeded_with_score")

    def test_run_report_status_fails_when_scoring_fails(self) -> None:
        similarity_report = {
            "status": "failed",
            "error": "prepared reference frame_count mismatch",
        }

        self.assertEqual(_resolve_run_report_status("succeeded", similarity_report), "failed")
        self.assertIn(
            "scoring failed: prepared reference frame_count mismatch",
            _resolve_run_report_summary("renderer completed successfully", similarity_report),
        )

    def test_validator_rejects_missing_prepared_reference_manifest(self) -> None:
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])

        validation = validate_job(
            self._build_job(reference_transition=reference_dir, frame_count=3),
            HARNESS_ROOT.parent,
            self._allowed_effects(),
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("prepared reference artifact" in issue.message for issue in validation.issues)
        )

    def test_validator_rejects_prepared_reference_frame_count_mismatch(self) -> None:
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        validation = validate_job(
            self._build_job(reference_transition=reference_dir, frame_count=2),
            HARNESS_ROOT.parent,
            self._allowed_effects(),
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("does not match render.frame_count" in issue.message for issue in validation.issues)
        )

    def test_validator_rejects_incomplete_prepared_reference_frames(self) -> None:
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        validation = validate_job(
            self._build_job(reference_transition=reference_dir, frame_count=3),
            HARNESS_ROOT.parent,
            self._allowed_effects(),
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("prepared reference contains 2 frame files" in issue.message for issue in validation.issues)
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

    def _build_job(self, reference_transition: Path, frame_count: int) -> RenderJob:
        source_a = self.root / "source_a"
        source_b = self.root / "source_b"
        self._write_bmp_sequence(source_a, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_bmp_sequence(source_b, [(255, 255, 255), (255, 255, 255), (255, 255, 255)])
        return RenderJob(
            job_name="validator_test",
            effect=EffectSpec(
                fx_id="CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                category="single_pass",
                effect_spec=None,
                uniforms={"progress": 0.0},
            ),
            inputs=InputSpec(
                source_a=str(source_a),
                source_b=str(source_b),
                reference_transition=str(reference_transition),
            ),
            render=RenderSettings(
                width=self.width,
                height=self.height,
                fps=30,
                frame_count=frame_count,
                output_format="png_sequence",
            ),
        )

    def _allowed_effects(self) -> dict:
        return {
            "allowed_categories": ["single_pass"],
            "required_uniforms": ["progress"],
        }


if __name__ == "__main__":
    unittest.main()
