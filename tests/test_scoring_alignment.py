from __future__ import annotations

import os
import json
import re
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch


HARNESS_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = HARNESS_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from overlay_harness.cli import _build_similarity_report
from overlay_harness.cli import _handle_analyze_transition
from overlay_harness.cli import _build_plan_comparison_report
from overlay_harness.cli import _build_run_evaluation_summary
from overlay_harness.cli import _handle_audit_effects
from overlay_harness.cli import _handle_index_effects
from overlay_harness.cli import _handle_flow
from overlay_harness.cli import _handle_analyze_sample_video
from overlay_harness.cli import _handle_sample_video
from overlay_harness.cli import _execute_job_command
from overlay_harness.cli import _summarize_retrieval_fields
from overlay_harness.cli import _summarize_retrieval_from_evaluation
from overlay_harness.cli import _summarize_smoke_test_retrieval
from overlay_harness.cli import _summarize_smoke_test_validation_retrieval
from overlay_harness.cli import _resolve_run_report_status
from overlay_harness.cli import _resolve_run_report_summary
from overlay_harness.effect_catalog import build_effect_catalog
from overlay_harness.effect_catalog import build_effect_catalog_audit
from overlay_harness.effect_catalog import load_effect_catalog
from overlay_harness.effect_catalog import select_effect_candidate
from overlay_harness.config import load_analysis_provider_config
from overlay_harness.analyzer import build_transition_analysis_provider_adapter
from overlay_harness.analyzer import build_transition_model_executor
from overlay_harness.analyzer import ModelBackedTransitionAnalysisProvider
from overlay_harness.analyzer import analyze_transition
from overlay_harness.analyzer import build_transition_analysis_artifact
from overlay_harness.analyzer import derive_analyzer_inputs_from_metadata
from overlay_harness.evaluator import score_frame_sequences
from overlay_harness.models import EffectSpec, InputSpec, RenderJob, RenderSettings
from overlay_harness.planner import build_recommended_plan
from overlay_harness.planner import GENERATED_EFFECT_GRAMMAR
from overlay_harness.planner import GENERATED_EFFECT_GRAMMAR_ALIASES
from overlay_harness.planner import GENERATED_EFFECT_STYLE_ALIASES
from overlay_harness.planner import GENERATED_EFFECT_PLACEHOLDER_MODES
from overlay_harness.planner import GENERATED_EFFECT_SUPPORTED_STYLES
from overlay_harness.planner import GENERATED_EFFECT_STYLES
from overlay_harness.planner import build_planned_job
from overlay_harness.planner import auto_styles
from overlay_harness.planner import resolve_auto_plan
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
        planning = {
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            }
        }

        summary = _build_run_evaluation_summary(
            Invocation(),
            similarity_report,
            self.root / "similarity_score.json",
            planning,
        )

        self.assertEqual(summary["overall_status"], "succeeded_with_score")
        self.assertEqual(summary["render"]["status"], "succeeded")
        self.assertEqual(summary["score"]["status"], "succeeded")
        self.assertEqual(summary["score"]["alignment_mode"], "prepared_reference_manifest")
        self.assertEqual(summary["score"]["frame_count"], 3)
        self.assertEqual(summary["score"]["report_file"], str(self.root / "similarity_score.json"))
        self.assertIsNone(summary["score"]["error"])
        self.assertIsNone(summary["score"]["ssim"])
        self.assertEqual(summary["planning"]["retrieval_status"], "retrieved")
        self.assertEqual(summary["planning"]["retrieval_effect_id"], "builtin-glitch")
        self.assertEqual(summary["planning"]["retrieval_mode"], "builtin-glitch")
        self.assertEqual(summary["planning"]["retrieval_match_kind"], "alias")
        self.assertEqual(summary["planning"]["retrieval_matched_style_hint"], "glitch")
        self.assertEqual(summary["planning"]["retrieval_candidate_count"], 1)

    def test_similarity_report_threshold_evaluation_passes_for_low_error(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        report = _build_similarity_report(
            repo_root=HARNESS_ROOT.parent,
            candidate=candidate_dir,
            reference=reference_dir,
            width=self.width,
            height=self.height,
            frame_count=3,
            output=self.root / "similarity_report_pass.json",
        )

        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["threshold_evaluation"]["status"], "passed")
        self.assertEqual(report["threshold_evaluation"]["checks"]["mse"]["status"], "pass")
        self.assertEqual(report["threshold_evaluation"]["checks"]["mae"]["status"], "pass")
        self.assertEqual(report["threshold_evaluation"]["checks"]["psnr_db"]["status"], "pass")
        self.assertEqual(report["threshold_evaluation"]["checks"]["ssim"]["status"], "pass")
        self.assertIn("ssim", report["score"])

    def test_similarity_report_threshold_evaluation_fails_for_high_error(self) -> None:
        candidate_dir = self.root / "candidate"
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(candidate_dir, [(255, 255, 255), (255, 255, 255), (255, 255, 255)])
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        report = _build_similarity_report(
            repo_root=HARNESS_ROOT.parent,
            candidate=candidate_dir,
            reference=reference_dir,
            width=self.width,
            height=self.height,
            frame_count=3,
            output=self.root / "similarity_report_fail.json",
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["threshold_evaluation"]["status"], "failed")
        self.assertEqual(report["threshold_evaluation"]["checks"]["mse"]["status"], "fail")
        self.assertEqual(report["threshold_evaluation"]["checks"]["mae"]["status"], "fail")
        self.assertEqual(report["threshold_evaluation"]["checks"]["psnr_db"]["status"], "fail")
        self.assertEqual(report["threshold_evaluation"]["checks"]["ssim"]["status"], "fail")

    def test_run_evaluation_summary_handles_missing_score(self) -> None:
        class Invocation:
            status = "blocked"
            exit_code = None
            produced_frame_count = 0
            expected_frame_count = 3
            message = "renderer executable is not available yet; render request recorded only"

        summary = _build_run_evaluation_summary(Invocation(), None, None, None)

        self.assertEqual(summary["overall_status"], "blocked")
        self.assertEqual(summary["render"]["status"], "blocked")
        self.assertIsNone(summary["score"]["status"])
        self.assertIsNone(summary["score"]["alignment_mode"])
        self.assertIsNone(summary["score"]["frame_count"])
        self.assertIsNone(summary["score"]["report_file"])
        self.assertIsNone(summary["score"]["error"])
        self.assertIsNone(summary["score"]["threshold_status"])
        self.assertIsNone(summary["score"]["threshold_checks"])

    def test_run_evaluation_summary_includes_fallback_reason(self) -> None:
        class Invocation:
            status = "blocked"
            exit_code = None
            produced_frame_count = 0
            expected_frame_count = 3
            message = "renderer executable is not available yet; render request recorded only"

        planning = {
            "retrieval": {
                "status": "not_found",
                "fallback_used": True,
                "fallback_mode": "generated-glitch-placeholder",
                "fallback_preset": None,
                "fallback_reason": "effect catalog is unavailable",
            }
        }

        summary = _build_run_evaluation_summary(Invocation(), None, None, planning)

        self.assertEqual(summary["planning"]["retrieval_status"], "not_found")
        self.assertTrue(summary["planning"]["retrieval_fallback_used"])
        self.assertEqual(summary["planning"]["retrieval_fallback_mode"], "generated-glitch-placeholder")
        self.assertIsNone(summary["planning"]["retrieval_fallback_preset"])
        self.assertEqual(summary["planning"]["retrieval_fallback_reason"], "effect catalog is unavailable")

    def test_run_evaluation_summary_includes_score_threshold_breakdown(self) -> None:
        class Invocation:
            status = "succeeded"
            exit_code = 0
            produced_frame_count = 3
            expected_frame_count = 3
            message = "renderer completed successfully"

        similarity_report = {
            "status": "failed",
            "error": "threshold check failed",
            "alignment": {"mode": "prepared_reference_manifest"},
            "score": {"frame_count": 3, "ssim": 0.81},
            "threshold_evaluation": {
                "status": "failed",
                "checks": {
                    "mse": {"status": "fail"},
                    "mae": {"status": "fail"},
                    "psnr_db": {"status": "fail"},
                    "ssim": {"status": "fail"},
                },
            },
        }

        summary = _build_run_evaluation_summary(Invocation(), similarity_report, Path("similarity_score.json"), None)

        self.assertEqual(summary["score"]["status"], "failed")
        self.assertEqual(summary["score"]["threshold_status"], "failed")
        self.assertEqual(summary["score"]["threshold_checks"]["ssim"]["status"], "fail")
        self.assertEqual(summary["score"]["ssim"], 0.81)

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

    def test_plan_comparison_report_includes_retrieval_summaries(self) -> None:
        report = _build_plan_comparison_report(
            analysis_file="harness/work/example/analysis.json",
            job_output=self.root / "job.json",
            plan_source="analysis_embedded_or_hint",
            selected_plan={
                "auto": True,
                "style": "generated-glitch",
                "input_kind": "real",
                "preset": "real-smoke-glitch",
                "mode": "builtin-glitch",
                "job_name": "example",
            },
            selected_plan_retrieval_summary={
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
            embedded_plan={
                "auto": True,
                "style": "generated-glitch",
                "input_kind": "real",
                "preset": "real-smoke-glitch",
                "mode": "builtin-glitch",
                "job_name": "example",
                "retrieval": {
                    "status": "retrieved",
                    "effect_id": "builtin-glitch",
                    "mode": "builtin-glitch",
                    "fallback_used": False,
                    "fallback_mode": "builtin-glitch",
                    "fallback_preset": "real-smoke-glitch",
                    "fallback_reason": None,
                    "match_kind": "alias",
                    "matched_style_hint": "glitch",
                    "candidate_count": 1,
                },
            },
            embedded_plan_summary={
                "auto": True,
                "style": "generated-glitch",
                "input_kind": "real",
                "preset": "real-smoke-glitch",
                "mode": "builtin-glitch",
                "job_name": "example",
            },
            recomputed_plan={
                "auto": True,
                "style": "generated-glitch",
                "input_kind": "real",
                "preset": "real-smoke-glitch",
                "mode": "builtin-glitch",
                "job_name": "example",
                "retrieval": {
                    "status": "retrieved",
                    "effect_id": "builtin-glitch",
                    "mode": "builtin-glitch",
                    "fallback_used": False,
                    "fallback_mode": "builtin-glitch",
                    "fallback_preset": "real-smoke-glitch",
                    "fallback_reason": None,
                    "match_kind": "alias",
                    "matched_style_hint": "glitch",
                    "candidate_count": 1,
                },
            },
            recomputed_plan_summary={
                "auto": True,
                "style": "generated-glitch",
                "input_kind": "real",
                "preset": "real-smoke-glitch",
                "mode": "builtin-glitch",
                "job_name": "example",
            },
            recompute_matches_embedded=True,
            validation_valid=True,
            issues=[],
        )

        self.assertEqual(report["selected_plan_retrieval_summary"]["effect_id"], "builtin-glitch")
        self.assertEqual(report["embedded_plan_retrieval_summary"]["match_kind"], "alias")
        self.assertEqual(report["recomputed_plan_retrieval_summary"]["candidate_count"], 1)

    def test_summarize_retrieval_from_evaluation(self) -> None:
        summary = _summarize_retrieval_from_evaluation(
            {
                "planning": {
                    "retrieval_status": "retrieved",
                    "retrieval_effect_id": "builtin-glitch",
                    "retrieval_mode": "builtin-glitch",
                    "retrieval_fallback_used": False,
                    "retrieval_fallback_mode": "builtin-glitch",
                    "retrieval_fallback_preset": "real-smoke-glitch",
                    "retrieval_fallback_reason": None,
                    "retrieval_match_kind": "alias",
                    "retrieval_matched_style_hint": "glitch",
                    "retrieval_candidate_count": 1,
                }
            }
        )

        self.assertEqual(summary["effect_id"], "builtin-glitch")
        self.assertEqual(summary["match_kind"], "alias")
        self.assertEqual(summary["candidate_count"], 1)

    def test_summarize_retrieval_fields(self) -> None:
        summary = _summarize_retrieval_fields(
            {
                "retrieval": {
                    "status": "retrieved",
                    "effect_id": "builtin-glitch",
                    "mode": "builtin-glitch",
                    "fallback_used": False,
                    "fallback_mode": "builtin-glitch",
                    "fallback_preset": "real-smoke-glitch",
                    "fallback_reason": None,
                    "match_kind": "alias",
                    "matched_style_hint": "glitch",
                    "candidate_count": 1,
                }
            }
        )

        self.assertEqual(summary["status"], "retrieved")
        self.assertEqual(summary["effect_id"], "builtin-glitch")
        self.assertEqual(summary["match_kind"], "alias")
        self.assertEqual(summary["candidate_count"], 1)

    def test_summarize_smoke_test_retrieval(self) -> None:
        summary = _summarize_smoke_test_retrieval(
            [
                {
                    "run_retrieval_summary": {
                        "status": "retrieved",
                        "fallback_used": False,
                    }
                },
                {
                    "run_retrieval_summary": {
                        "status": "not_found",
                        "fallback_used": True,
                    }
                },
                {
                    "run_retrieval_summary": None,
                },
            ]
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary["job_count"], 3)
        self.assertEqual(summary["retrieved_count"], 1)
        self.assertEqual(summary["not_found_count"], 1)
        self.assertEqual(summary["fallback_used_count"], 1)

    def test_summarize_smoke_test_validation_retrieval(self) -> None:
        summary = _summarize_smoke_test_validation_retrieval(
            [
                {
                    "validate_retrieval_summary": {
                        "status": "retrieved",
                        "fallback_used": False,
                    }
                },
                {
                    "validate_retrieval_summary": {
                        "status": "not_found",
                        "fallback_used": True,
                    }
                },
                {
                    "validate_retrieval_summary": None,
                },
            ]
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary["job_count"], 3)
        self.assertEqual(summary["retrieved_count"], 1)
        self.assertEqual(summary["not_found_count"], 1)
        self.assertEqual(summary["fallback_used_count"], 1)

    def test_run_command_result_includes_planning_retrieval_summary(self) -> None:
        job_path = self.root / "job.json"
        job_data = json.loads((HARNESS_ROOT.parent / "harness/examples/render_job.sample.json").read_text(encoding="utf-8"))
        job_data["job_name"] = f"{job_data['job_name']}_{uuid4().hex}"
        job_data["planning"] = {
            "auto": True,
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
        }
        job_path.write_text(json.dumps(job_data, indent=2), encoding="utf-8")

        result = _execute_job_command(
            repo_root=HARNESS_ROOT.parent,
            harness_root=HARNESS_ROOT,
            config_dir=HARNESS_ROOT / "configs",
            job_path=job_path,
            command_name="run",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIsNotNone(result["planning_retrieval_summary"])
        self.assertEqual(result["planning_retrieval_summary"]["effect_id"], "builtin-glitch")
        self.assertEqual(result["planning_retrieval_summary"]["match_kind"], "alias")

    def test_validate_command_result_includes_planning_retrieval_summary(self) -> None:
        job_path = self.root / "job.json"
        job_data = json.loads((HARNESS_ROOT.parent / "harness/examples/render_job.sample.json").read_text(encoding="utf-8"))
        job_data["job_name"] = f"{job_data['job_name']}_{uuid4().hex}"
        job_data["planning"] = {
            "auto": True,
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
        }
        job_path.write_text(json.dumps(job_data, indent=2), encoding="utf-8")

        result = _execute_job_command(
            repo_root=HARNESS_ROOT.parent,
            harness_root=HARNESS_ROOT,
            config_dir=HARNESS_ROOT / "configs",
            job_path=job_path,
            command_name="validate",
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertIsNotNone(result["planning_retrieval_summary"])
        self.assertEqual(result["planning_retrieval_summary"]["effect_id"], "builtin-glitch")

    def test_smoke_test_job_result_includes_validation_retrieval_summary(self) -> None:
        results = [
            {
                "job": "harness/examples/render_job.sample.json",
                "validate_exit_code": 0,
                "validation_valid": True,
                "validate_retrieval_summary": {
                    "status": "retrieved",
                    "fallback_used": False,
                },
            },
            {
                "job": "harness/examples/render_job.effect_spec.sample.json",
                "validate_exit_code": 0,
                "validation_valid": True,
                "validate_retrieval_summary": {
                    "status": "not_found",
                    "fallback_used": True,
                },
            },
        ]

        summary = _summarize_smoke_test_validation_retrieval(results)

        self.assertEqual(summary["job_count"], 2)
        self.assertEqual(summary["retrieved_count"], 1)
        self.assertEqual(summary["not_found_count"], 1)

    def test_prepare_command_result_includes_planning_retrieval_summary(self) -> None:
        job_path = self.root / "job.json"
        job_data = json.loads((HARNESS_ROOT.parent / "harness/examples/render_job.sample.json").read_text(encoding="utf-8"))
        job_data["job_name"] = f"{job_data['job_name']}_{uuid4().hex}"
        job_data["planning"] = {
            "auto": True,
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
        }
        job_path.write_text(json.dumps(job_data, indent=2), encoding="utf-8")

        result = _execute_job_command(
            repo_root=HARNESS_ROOT.parent,
            harness_root=HARNESS_ROOT,
            config_dir=HARNESS_ROOT / "configs",
            job_path=job_path,
            command_name="prepare",
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertIsNotNone(result["workspace"])
        self.assertIsNotNone(result["planning_retrieval_summary"])
        self.assertEqual(result["planning_retrieval_summary"]["match_kind"], "alias")

    def test_flow_command_writes_end_to_end_report(self) -> None:
        output_root = self.root / "flow_output"
        args = SimpleNamespace(
            transition_video=str(HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"),
            source_a=str(HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"),
            source_b=str(HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"),
            output_root=str(output_root),
            renderer=None,
            style_hint="generated-glitch",
            intent=None,
            prefer_generated=False,
            input_kind="real",
            job_name="flow_job",
            width=1920,
            height=1080,
            fps=30,
            frame_count=None,
            target_frame_count=30,
            analysis_width=64,
            analysis_height=36,
            ffmpeg=None,
            analysis_provider_kind="deterministic_rules",
            analysis_provider_name=None,
            analysis_provider_mode="deterministic",
            effect_spec_output=None,
        )

        reference_result = SimpleNamespace(
            output_dir=output_root / "transition_flow_stub" / "reference_transition",
            frame_count=30,
            message="prepared 30 normalized reference frames",
            manifest_file=output_root / "transition_flow_stub" / "reference_transition" / "reference_transition_manifest.json",
            detected_start_frame=1,
            detected_end_frame=30,
            detected_frame_count=30,
        )
        planning = {
            "auto": True,
            "style": "generated-glitch",
            "input_kind": "real",
            "preset": "real-smoke-glitch",
            "mode": "builtin-glitch",
            "job_name": "flow_job",
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
        }
        job = SimpleNamespace(
            job_name="flow_job",
            render=SimpleNamespace(frame_count=30),
            to_dict=lambda: {
                "job_name": "flow_job",
                "planning": planning,
                "render": {"frame_count": 30},
            },
        )
        run_result = {
            "exit_code": 0,
            "validation_valid": True,
            "job_path": str(output_root / "transition_flow_stub" / "planned.render_job.json"),
            "workspace": str(output_root / "transition_flow_stub" / "workspace"),
            "report": str(output_root / "transition_flow_stub" / "workspace" / "reports" / "run_report.json"),
            "demo_video_file": str(output_root / "transition_flow_stub" / "workspace" / "artifacts" / "rendered.mp4"),
            "request_file": str(output_root / "transition_flow_stub" / "workspace" / "render" / "render_request.json"),
            "renderer_result_file": str(output_root / "transition_flow_stub" / "workspace" / "render" / "renderer_result.json"),
            "status": "succeeded",
            "summary": "renderer completed successfully",
            "evaluation": {
                "score": {
                    "report_file": str(output_root / "transition_flow_stub" / "workspace" / "reports" / "similarity_score.json"),
                    "status": "succeeded",
                    "error": None,
                },
                "planning": {
                    "retrieval_status": "retrieved",
                    "retrieval_effect_id": "builtin-glitch",
                    "retrieval_mode": "builtin-glitch",
                    "retrieval_fallback_used": False,
                    "retrieval_fallback_mode": "builtin-glitch",
                    "retrieval_fallback_preset": "real-smoke-glitch",
                    "retrieval_fallback_reason": None,
                    "retrieval_match_kind": "alias",
                    "retrieval_matched_style_hint": "glitch",
                    "retrieval_candidate_count": 1,
                },
                "overall_status": "succeeded_with_score",
            },
        }

        with (
            patch("overlay_harness.cli.prepare_reference_transition", return_value=reference_result),
            patch(
                "overlay_harness.cli.analyze_transition",
                return_value={
                    "style_hint": "generated-glitch",
                    "input_kind": "real",
                    "reference_transition": str(reference_result.output_dir),
                    "job_name": "flow_job",
                    "notes": "analyzer selected generated-glitch because intent mentioned glitch",
                    "analysis": {"style_reason": "intent mentioned glitch"},
                },
            ),
            patch(
                "overlay_harness.cli.build_transition_analysis_artifact",
                return_value={
                    "artifact_type": "transition_analysis",
                    "artifact_version": 2,
                    "sources": {
                        "source_a": "harness/examples/inputs/source_a_real",
                        "source_b": "harness/examples/inputs/source_b_real",
                        "reference_transition": str(reference_result.output_dir),
                    },
                    "facts": {
                        "resolved": {
                            "style_hint": "generated-glitch",
                            "input_kind": "real",
                            "job_name": "flow_job",
                        },
                        "analyzer_inputs": {"flow": True},
                        "analysis_mode": "deterministic_rules",
                        "transition_video_analysis": {
                            "source": "transition_video",
                            "analysis_engine": "deterministic_rules_v1",
                            "reference_transition": str(reference_result.output_dir),
                            "transition_video": "harness/sample_glitch.mp4",
                            "transition_window": {
                                "frame_count": 30,
                                "detected_start_frame": 0,
                                "detected_end_frame": 29,
                                "detected_frame_count": 30,
                                "message": "prepared",
                            },
                            "transition_progression": {
                                "window_span_frames": 30,
                                "window_midpoint_frame": 14,
                                "window_coverage_ratio": 1.0,
                                "window_start_progress": 0.0,
                                "window_end_progress": 1.0,
                                "window_message": "prepared",
                            },
                        },
                        "transition_summary": {"combined_motion_level": "high"},
                        "transition_window": {
                            "frame_count": 30,
                            "detected_start_frame": 0,
                            "detected_end_frame": 29,
                            "detected_frame_count": 30,
                            "message": "prepared",
                        },
                        "transition_progression": {
                            "window_span_frames": 30,
                            "window_midpoint_frame": 14,
                            "window_coverage_ratio": 1.0,
                            "window_start_progress": 0.0,
                            "window_end_progress": 1.0,
                            "window_message": "prepared",
                        },
                    },
                    "planning_recommendation": {
                        **planning,
                        "analysis_engine": "deterministic_rules_v1",
                    },
                },
            ),
            patch("overlay_harness.cli.resolve_planned_frame_count", return_value=(30, "reference_transition_manifest")),
            patch("overlay_harness.cli.build_planned_job", return_value=(job, {"effect": "spec"})),
            patch("overlay_harness.cli.validate_job", return_value=SimpleNamespace(is_valid=True, issues=[])),
            patch("overlay_harness.cli._execute_job_command", return_value=run_result),
            patch("builtins.print") as print_mock,
        ):
            exit_code = _handle_flow(args, HARNESS_ROOT.parent, HARNESS_ROOT, HARNESS_ROOT / "configs", None)

        self.assertEqual(exit_code, 0)
        report_files = list(output_root.glob("transition_flow_*/flow_report.json"))
        self.assertEqual(len(report_files), 1)
        with report_files[0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["report_type"], "flow_report")
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["data"]["planning"]["retrieval_summary"]["effect_id"], "builtin-glitch")
        self.assertEqual(payload["data"]["run"]["status"], "succeeded")
        self.assertEqual(payload["data"]["reference_transition"]["frame_count"], 30)
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["analysis_mode"], "deterministic_rules")
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_summary"]["combined_motion_level"], "high")
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_window"]["frame_count"], 30)
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_progression"]["window_span_frames"], 30)
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_progression"]["window_start_progress"], 0.0)
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_video_analysis"]["source"], "transition_video")
        self.assertEqual(payload["data"]["analysis_artifact"]["facts"]["transition_video_analysis"]["analysis_engine"], "deterministic_rules_v1")
        self.assertEqual(
            payload["data"]["analysis_artifact"]["facts"]["transition_video_analysis"]["transition_video"],
            "harness/sample_glitch.mp4",
        )
        self.assertEqual(payload["data"]["analysis_context"]["analysis_source"], "transition_video")
        self.assertEqual(payload["data"]["analysis_context"]["analysis_engine"], "deterministic_rules_v1")
        self.assertEqual(payload["data"]["analysis_context"]["transition_video"], "harness/examples/inputs/source_a_real")
        self.assertEqual(payload["data"]["analysis_artifact"]["planning_recommendation"]["producer"], "transition_video_analysis")
        self.assertEqual(payload["data"]["analysis_artifact"]["planning_recommendation"]["analysis_engine"], "deterministic_rules_v1")
        self.assertEqual(payload["data"]["analysis_artifact"]["planning_recommendation"]["transition_planning_hint"]["analysis_source"], "transition_video")
        self.assertEqual(payload["data"]["planning"]["mode"], "builtin-glitch")
        self.assertEqual(payload["data"]["run"]["evaluation"]["overall_status"], "succeeded_with_score")
        self.assertEqual(payload["data"]["run"]["evaluation"]["score"]["status"], "succeeded")
        self.assertIsNone(payload["data"]["run"]["evaluation"]["score"]["error"])
        self.assertEqual(payload["data"]["workspace_paths"]["flow_root"], str(output_root / report_files[0].parent.name))
        self.assertEqual(
            payload["data"]["workspace_paths"]["similarity_report_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "reports" / "similarity_score.json"),
        )
        self.assertEqual(
            payload["data"]["workspace_paths"]["render_request_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "render" / "render_request.json"),
        )
        self.assertEqual(
            payload["data"]["workspace_paths"]["renderer_result_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "render" / "renderer_result.json"),
        )
        self.assertEqual(payload["data"]["workspace_paths"]["demo_video_file"], str(run_result["demo_video_file"]))
        self.assertEqual(payload["data"]["artifacts"]["run_report"], str(run_result["report"]))
        self.assertEqual(payload["data"]["artifacts"]["demo_video_file"], str(run_result["demo_video_file"]))
        self.assertEqual(payload["data"]["run"]["demo_video_file"], str(run_result["demo_video_file"]))
        self.assertEqual(payload["data"]["artifacts"]["analysis_file"], str(output_root / report_files[0].parent.name / "transition_analysis.json"))
        stdout_payload = json.loads(print_mock.call_args.args[0])
        self.assertEqual(stdout_payload["workspace_paths"]["flow_root"], str(output_root / report_files[0].parent.name))
        self.assertEqual(
            stdout_payload["workspace_paths"]["similarity_report_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "reports" / "similarity_score.json"),
        )
        self.assertEqual(
            stdout_payload["workspace_paths"]["render_request_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "render" / "render_request.json"),
        )
        self.assertEqual(
            stdout_payload["workspace_paths"]["renderer_result_file"],
            str(output_root / "transition_flow_stub" / "workspace" / "render" / "renderer_result.json"),
        )
        self.assertEqual(stdout_payload["workspace_paths"]["demo_video_file"], str(run_result["demo_video_file"]))

    def test_analyze_sample_video_command_writes_video_backed_analysis(self) -> None:
        output_root = self.root / "sample_video_analysis_output"
        output_root.mkdir(parents=True, exist_ok=True)

        args = SimpleNamespace(
            transition_video="harness/sample_glitch.mp4",
            source_a="harness/examples/inputs/source_a_real",
            source_b="harness/examples/inputs/source_b_real",
            output_root=str(output_root),
            style_hint=None,
            intent="generated glitch transition",
            prefer_generated=False,
            input_kind="auto",
            job_name="sample_analysis_job",
            width=1920,
            height=1080,
            fps=30,
            target_frame_count=30,
            analysis_width=64,
            analysis_height=36,
            ffmpeg=None,
            analysis_output=None,
            comparison_output=None,
        )
        reference_result = SimpleNamespace(
            output_dir=output_root / "sample_video_analysis_stub" / "reference_transition",
            manifest_file=output_root / "sample_video_analysis_stub" / "reference_transition" / "reference_transition_manifest.json",
            frame_count=30,
            message="prepared",
            detected_start_frame=0,
            detected_end_frame=29,
            detected_frame_count=30,
        )

        with (
            patch("overlay_harness.cli.prepare_reference_transition", return_value=reference_result),
            patch(
                "overlay_harness.cli.analyze_transition",
                return_value={
                    "style_hint": "generated-noise",
                    "input_kind": "real",
                    "reference_transition": str(reference_result.output_dir),
                    "job_name": "sample_analysis_job",
                    "notes": "analyzer selected generated-noise because intent mentioned glitch",
                    "analysis": {"style_reason": "intent mentioned glitch"},
                },
            ),
            patch(
                "overlay_harness.cli.build_transition_analysis_artifact",
                return_value={
                    "artifact_type": "transition_analysis",
                    "artifact_version": 2,
                    "sources": {
                        "source_a": "harness/examples/inputs/source_a_real",
                        "source_b": "harness/examples/inputs/source_b_real",
                        "reference_transition": str(reference_result.output_dir),
                    },
                    "facts": {
                        "resolved": {
                            "style_hint": "generated-noise",
                            "input_kind": "real",
                            "job_name": "sample_analysis_job",
                            "style_reason": "intent mentioned glitch",
                        },
                        "analyzer_inputs": {"sample_video": True},
                        "analysis_mode": "deterministic_rules",
                        "transition_summary": {"combined_motion_level": "high"},
                        "transition_window": {
                            "frame_count": 30,
                            "detected_start_frame": 0,
                            "detected_end_frame": 29,
                            "detected_frame_count": 30,
                            "message": "prepared",
                        },
                        "transition_progression": {
                            "window_span_frames": 30,
                            "window_midpoint_frame": 14,
                            "window_coverage_ratio": 1.0,
                            "window_start_progress": 0.0,
                            "window_end_progress": 1.0,
                            "window_message": "prepared",
                        },
                    },
                    "planning_recommendation": {"mode": "generated-glitch-placeholder", "analysis_engine": "deterministic_rules_v1"},
                },
            ),
        ):
            exit_code = _handle_analyze_sample_video(args, HARNESS_ROOT.parent)

        self.assertEqual(exit_code, 0)
        report_files = list(output_root.glob("sample_video_analysis_*/transition_analysis.json"))
        self.assertEqual(len(report_files), 1)
        with report_files[0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["artifact_type"], "transition_analysis")
        self.assertEqual(payload["facts"]["resolved"]["style_hint"], "generated-noise")
        self.assertTrue(payload["facts"]["analyzer_inputs"]["sample_video"])
        self.assertEqual(payload["facts"]["analysis_mode"], "deterministic_rules")
        self.assertEqual(payload["facts"]["transition_summary"]["combined_motion_level"], "high")
        self.assertEqual(payload["facts"]["transition_window"]["frame_count"], 30)
        self.assertEqual(payload["facts"]["transition_progression"]["window_span_frames"], 30)
        self.assertEqual(payload["facts"]["transition_progression"]["window_end_progress"], 1.0)
        self.assertEqual(payload["planning_recommendation"]["analysis_engine"], "deterministic_rules_v1")

    def test_analyze_transition_command_records_requested_provider_metadata(self) -> None:
        output_root = self.root / "analysis_provider_output"
        output_root.mkdir(parents=True, exist_ok=True)

        args = SimpleNamespace(
            source_a="harness/examples/inputs/source_a_real",
            source_b="harness/examples/inputs/source_b_real",
            hint_output=str(output_root / "transition_hint.json"),
            analysis_output=str(output_root / "transition_analysis.json"),
            comparison_output=None,
            clip_metadata_file=None,
            style_hint=None,
            intent="generated glitch transition",
            prefer_generated=False,
            analysis_provider_kind="model_backed",
            analysis_provider_name="openai-transition-model",
            analysis_provider_mode="vision",
            input_kind="auto",
            reference_transition=None,
            job_name="analysis_provider_job",
        )

        with patch("builtins.print") as print_mock:
            _handle_analyze_transition(args, HARNESS_ROOT.parent)
        with (output_root / "transition_analysis.json").open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        stdout_payload = json.loads(print_mock.call_args.args[0])

        self.assertEqual(payload["facts"]["analysis_provider_request"]["kind"], "model_backed")
        self.assertEqual(payload["facts"]["analysis_provider_request"]["name"], "openai-transition-model")
        self.assertEqual(payload["facts"]["analysis_provider_request"]["mode"], "vision")
        self.assertEqual(payload["facts"]["analysis_provider_resolution"]["status"], "fallback_to_deterministic")
        self.assertEqual(payload["facts"]["analysis_provider_resolution"]["configuration"]["loaded"], True)
        self.assertEqual(payload["facts"]["analysis_provider_resolution"]["configuration"]["config_source"], "repo:configs/analysis_provider.json")
        self.assertEqual(payload["facts"]["analysis_provider_resolution"]["configuration"]["model_backed_enabled"], False)
        self.assertEqual(payload["facts"]["analysis_provider_runtime"]["execution"]["execution_mode"], "deterministic_fallback")
        self.assertEqual(payload["facts"]["analysis_provider_runtime"]["execution"]["implementation_status"], "fallback_only")
        self.assertEqual(payload["facts"]["analysis_provider"]["kind"], "deterministic_rules")
        self.assertEqual(payload["facts"]["analysis_provider"]["name"], "deterministic_rules_v1")
        self.assertEqual(stdout_payload["analysis_provider_request"]["kind"], "model_backed")
        self.assertEqual(stdout_payload["analysis_provider_resolution"]["status"], "fallback_to_deterministic")
        self.assertEqual(stdout_payload["analysis_provider_configuration"]["loaded"], True)
        self.assertEqual(stdout_payload["analysis_model_execution_contract"]["contract_type"], "transition_analysis_model_execution")

    def test_analysis_provider_config_is_loaded(self) -> None:
        config = load_analysis_provider_config(HARNESS_ROOT / "configs")

        self.assertIsNotNone(config)
        self.assertEqual(config["config_type"], "analysis_provider_config")
        self.assertEqual(config["default_provider"]["kind"], "deterministic_rules")
        self.assertEqual(config["model_backed_provider"]["kind"], "model_backed")

    def test_analysis_provider_config_uses_environment_override(self) -> None:
        override_path = self.root / "analysis_provider.override.json"
        override_path.write_text(
            json.dumps(
                {
                    "config_type": "analysis_provider_config",
                    "config_version": 2,
                    "default_provider": {
                        "kind": "deterministic_rules",
                        "name": "deterministic_rules_v2",
                        "mode": "deterministic",
                    },
                    "model_backed_provider": {
                        "enabled": True,
                        "kind": "model_backed",
                        "name": "openai-transition-model-v2",
                        "mode": "vision",
                        "source": "env:HARNESS_ANALYSIS_PROVIDER_CONFIG",
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HARNESS_ANALYSIS_PROVIDER_CONFIG": str(override_path)}):
            config = load_analysis_provider_config(HARNESS_ROOT / "configs")

        self.assertIsNotNone(config)
        self.assertEqual(config["config_version"], 2)
        self.assertEqual(config["config_source"], "env:HARNESS_ANALYSIS_PROVIDER_CONFIG")
        self.assertEqual(config["config_path"], str(override_path))
        self.assertTrue(config["model_backed_provider"]["enabled"])

    def test_analysis_provider_config_rejects_invalid_schema(self) -> None:
        invalid_path = self.root / "analysis_provider.invalid.json"
        invalid_path.write_text(
            json.dumps(
                {
                    "config_type": "analysis_provider_config",
                    "config_version": 1,
                    "default_provider": {
                        "kind": "deterministic_rules",
                        "name": "deterministic_rules_v1",
                        "mode": "deterministic",
                    },
                    "model_backed_provider": {
                        "enabled": "yes",
                        "kind": "model_backed",
                        "name": "openai-transition-model",
                        "mode": "vision",
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HARNESS_ANALYSIS_PROVIDER_CONFIG": str(invalid_path)}):
            with self.assertRaises(ValueError):
                load_analysis_provider_config(HARNESS_ROOT / "configs")

    def test_default_analysis_provider_adapter_is_deterministic(self) -> None:
        provider = build_transition_analysis_provider_adapter()

        self.assertEqual(provider.__class__.__name__, "DeterministicTransitionAnalysisProvider")

    def test_default_model_executor_is_deterministic_fallback(self) -> None:
        executor = build_transition_model_executor()

        self.assertEqual(executor.__class__.__name__, "DeterministicTransitionModelExecutor")

    def test_default_model_executor_emits_versioned_contract(self) -> None:
        executor = build_transition_model_executor()
        model_result = executor.execute_model_request(
            {
                "contract_type": "transition_analysis_model_execution",
                "contract_version": 1,
                "provider": {
                    "kind": "model_backed",
                    "name": "openai-transition-model",
                    "mode": "vision",
                },
                "inputs": {
                    "repo_root": str(HARNESS_ROOT.parent),
                    "source_a": str(HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"),
                    "source_b": str(HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"),
                    "input_kind": "auto",
                    "style_hint": None,
                    "intent": "generated glitch transition",
                    "prefer_generated": False,
                    "reference_transition": None,
                    "job_name": "versioned_contract_executor_job",
                },
                "analyzer_inputs": {
                    "input_kind": "auto",
                    "style_hint": None,
                    "intent": "generated glitch transition",
                    "prefer_generated": False,
                    "reference_transition": None,
                    "job_name": "versioned_contract_executor_job",
                },
                "execution": {
                    "contract_type": "transition_analysis_model_execution",
                    "contract_version": 1,
                    "expected_status": "delegated_to_deterministic_fallback",
                    "result_contract": {
                        "style_hint": "str",
                        "input_kind": "str",
                        "reference_transition": "str | None",
                        "job_name": "str | None",
                        "notes": "str",
                        "analysis": "dict[str, Any]",
                    },
                },
            }
        )

        self.assertEqual(model_result["contract_type"], "transition_analysis_model_execution")
        self.assertEqual(model_result["contract_version"], 1)
        self.assertEqual(model_result["status"], "delegated_to_deterministic_fallback")
        self.assertEqual(model_result["execution_mode"], "pending_model_execution")
        self.assertIn("hint", model_result)

    def test_enabled_model_backed_config_selects_model_backed_adapter(self) -> None:
        override_path = self.root / "analysis_provider.enabled.json"
        override_path.write_text(
            json.dumps(
                {
                    "config_type": "analysis_provider_config",
                    "config_version": 1,
                    "default_provider": {
                        "kind": "deterministic_rules",
                        "name": "deterministic_rules_v1",
                        "mode": "deterministic",
                    },
                    "model_backed_provider": {
                        "enabled": True,
                        "kind": "model_backed",
                        "name": "openai-transition-model",
                        "mode": "vision",
                        "source": "env:HARNESS_ANALYSIS_PROVIDER_CONFIG",
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HARNESS_ANALYSIS_PROVIDER_CONFIG": str(override_path)}):
            config = load_analysis_provider_config(HARNESS_ROOT / "configs")
            provider = build_transition_analysis_provider_adapter(
                {"kind": "model_backed", "name": "openai-transition-model", "mode": "vision"},
                config,
            )
            hint = analyze_transition(
                repo_root=HARNESS_ROOT.parent,
                source_a=HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real",
                source_b=HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real",
                input_kind="auto",
                style_hint=None,
                intent="generated glitch transition",
                prefer_generated=False,
                reference_transition=None,
                job_name="enabled_model_backed_adapter",
                provider_request={
                    "kind": "model_backed",
                    "name": "openai-transition-model",
                    "mode": "vision",
                },
                provider_configuration=config,
            )

        self.assertEqual(provider.__class__.__name__, "ModelBackedTransitionAnalysisProvider")
        self.assertEqual(hint["analysis_provider"]["kind"], "model_backed")
        self.assertEqual(hint["analysis_provider"]["name"], "openai-transition-model")
        self.assertEqual(hint["analysis_provider"]["mode"], "pending_model_execution")
        self.assertEqual(hint["analysis"]["model_execution_status"], "delegated_to_deterministic_fallback")
        self.assertEqual(hint["analysis"]["model_execution"]["execution_mode"], "pending_model_execution")
        self.assertEqual(hint["analysis"]["model_execution"]["request"]["provider"]["name"], "openai-transition-model")

        artifact = build_transition_analysis_artifact(
            repo_root=HARNESS_ROOT.parent,
            source_a=HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real",
            source_b=HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real",
            analyzer_inputs={
                "analysis_provider_kind": "model_backed",
                "analysis_provider_name": "openai-transition-model",
                "analysis_provider_mode": "vision",
                "analysis_mode": "deterministic_rules",
                "analysis_provider_configuration": config,
            },
            hint=hint,
        )

        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["adapter"]["kind"], "model_backed")
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["adapter"]["status"], "model_backed_adapter_skeleton")
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["delegation"]["path"], "model_backed_skeleton")
        self.assertFalse(artifact["facts"]["analysis_provider_runtime"]["delegation"]["model_execution_ready"])
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["execution"]["implementation_status"], "pending_model_execution")
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["execution"]["execution_mode"], "deterministic_fallback_pending_model_execution")
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["execution"]["contract_version"], 1)
        self.assertEqual(
            artifact["facts"]["analysis_provider_runtime"]["execution"]["model_execution_contract"]["contract_type"],
            "transition_analysis_model_execution",
        )
        self.assertEqual(
            artifact["facts"]["analysis_provider_runtime"]["execution"]["model_execution_contract"]["contract_version"],
            1,
        )
        self.assertEqual(
            artifact["facts"]["analysis_provider_runtime"]["execution"]["model_execution_contract"]["result_contract"]["hint"],
            "dict[str, Any]",
        )

    def test_model_backed_provider_uses_custom_executor_boundary(self) -> None:
        class CustomExecutor:
            def execute_model_request(self, model_request):
                return {
                    "contract_type": "transition_analysis_model_execution",
                    "contract_version": 1,
                    "status": "custom_model_execution",
                    "execution_mode": "custom_model_execution",
                    "notes": "custom executor boundary used",
                    "hint": {
                        "analysis_provider": {
                            "kind": "model_backed",
                            "name": model_request["provider"]["name"],
                            "mode": "custom_model_execution",
                        },
                        "style_hint": "generated-noise",
                        "input_kind": model_request["inputs"]["input_kind"],
                        "reference_transition": model_request["inputs"]["reference_transition"],
                        "job_name": model_request["inputs"]["job_name"],
                        "notes": "custom executor boundary used",
                        "analysis": {
                            "style_reason": "custom executor boundary used",
                            "model_execution": model_request,
                        },
                    },
                }

        provider = ModelBackedTransitionAnalysisProvider(
            resolved_name="openai-transition-model",
            model_executor=CustomExecutor(),
        )
        hint = provider.analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real",
            source_b=HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real",
            input_kind="auto",
            style_hint=None,
            intent="generated noise transition",
            prefer_generated=False,
            reference_transition=None,
            job_name="custom_model_executor_job",
            analyzer_inputs={
                "input_kind": "auto",
                "style_hint": None,
                "intent": "generated noise transition",
                "prefer_generated": False,
                "reference_transition": None,
                "job_name": "custom_model_executor_job",
            },
        )

        self.assertEqual(hint["analysis_provider"]["kind"], "model_backed")
        self.assertEqual(hint["analysis_provider"]["mode"], "custom_model_execution")
        self.assertEqual(hint["analysis"]["model_execution_status"], "custom_model_execution")
        self.assertEqual(hint["analysis"]["model_execution_mode"], "custom_model_execution")
        self.assertEqual(hint["analysis"]["model_execution"]["request"]["provider"]["name"], "openai-transition-model")
        self.assertEqual(hint["analysis"]["model_execution"]["status"], "custom_model_execution")

    def test_model_backed_provider_rejects_invalid_executor_result(self) -> None:
        class InvalidExecutor:
            def execute_model_request(self, model_request):
                return {
                    "status": "custom_model_execution",
                    "execution_mode": "custom_model_execution",
                    "notes": "missing hint",
                }

        provider = ModelBackedTransitionAnalysisProvider(
            resolved_name="openai-transition-model",
            model_executor=InvalidExecutor(),
        )

        with self.assertRaises(ValueError):
            provider.analyze_transition(
                repo_root=HARNESS_ROOT.parent,
                source_a=HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real",
                source_b=HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real",
                input_kind="auto",
                style_hint=None,
                intent="generated noise transition",
                prefer_generated=False,
                reference_transition=None,
                job_name="invalid_model_executor_job",
                analyzer_inputs={
                    "input_kind": "auto",
                    "style_hint": None,
                    "intent": "generated noise transition",
                    "prefer_generated": False,
                    "reference_transition": None,
                    "job_name": "invalid_model_executor_job",
                },
            )

    def _flow_command_persists_end_to_end_evaluation_summary(self) -> None:
        output_root = self.root / "flow_evaluation_output"
        output_root.mkdir(parents=True, exist_ok=True)

        args = SimpleNamespace(
            transition_video="harness/sample_glitch.mp4",
            source_a="harness/examples/inputs/source_a_real",
            source_b="harness/examples/inputs/source_b_real",
            output_root=str(output_root),
            style_hint=None,
            intent="generated glitch transition",
            prefer_generated=False,
            input_kind="auto",
            job_name="flow_eval_job",
            width=1920,
            height=1080,
            fps=30,
            frame_count=30,
            analysis_width=64,
            analysis_height=36,
            ffmpeg=None,
            analysis_provider_kind="deterministic_rules",
            analysis_provider_name=None,
            analysis_provider_mode="deterministic",
            renderer=None,
            effect_spec_output=None,
        )
        reference_result = SimpleNamespace(
            output_dir=output_root / "transition_flow_stub" / "reference_transition",
            manifest_file=output_root / "transition_flow_stub" / "reference_transition" / "reference_transition_manifest.json",
            frame_count=30,
            message="prepared",
            detected_start_frame=0,
            detected_end_frame=29,
            detected_frame_count=30,
        )
        planning = {
            "auto": True,
            "style": "generated-glitch",
            "input_kind": "real",
            "preset": "real-smoke-glitch",
            "mode": "builtin-glitch",
            "job_name": "flow_eval_job",
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
                "fallback_used": False,
                "fallback_mode": "builtin-glitch",
                "fallback_preset": "real-smoke-glitch",
                "fallback_reason": None,
                "match_kind": "alias",
                "matched_style_hint": "glitch",
                "candidate_count": 1,
            },
        }
        job = SimpleNamespace(
            job_name="flow_eval_job",
            effect=SimpleNamespace(fx_id="builtin-glitch"),
            render=SimpleNamespace(frame_count=30, fps=30),
            planning=planning,
            to_dict=lambda: {
                "job_name": "flow_eval_job",
                "planning": planning,
                "render": {"frame_count": 30, "fps": 30},
            },
        )
        run_result = {
            "exit_code": 0,
            "workspace": str(output_root / "transition_flow_stub" / "workspace"),
            "report": str(output_root / "transition_flow_stub" / "workspace" / "reports" / "run_report.json"),
            "request_file": str(output_root / "transition_flow_stub" / "workspace" / "render" / "render_request.json"),
            "renderer_result_file": str(output_root / "transition_flow_stub" / "workspace" / "render" / "renderer_result.json"),
            "status": "succeeded",
            "summary": "renderer completed successfully",
            "demo_video_file": str(output_root / "transition_flow_stub" / "workspace" / "artifacts" / "rendered.mp4"),
            "evaluation": {
                "score": {
                    "report_file": str(output_root / "transition_flow_stub" / "workspace" / "reports" / "similarity_score.json"),
                    "status": "succeeded",
                    "alignment_mode": "prepared_reference_manifest",
                    "frame_count": 30,
                    "error": None,
                },
                "planning": {
                    "retrieval_status": "retrieved",
                    "retrieval_effect_id": "builtin-glitch",
                    "retrieval_mode": "builtin-glitch",
                    "retrieval_fallback_used": False,
                    "retrieval_fallback_mode": "builtin-glitch",
                    "retrieval_fallback_preset": "real-smoke-glitch",
                    "retrieval_fallback_reason": None,
                    "retrieval_match_kind": "alias",
                    "retrieval_matched_style_hint": "glitch",
                    "retrieval_candidate_count": 1,
                },
            },
        }

        with (
            patch("overlay_harness.cli.prepare_reference_transition", return_value=reference_result),
            patch(
                "overlay_harness.cli.analyze_transition",
                return_value={
                    "style_hint": "generated-glitch",
                    "input_kind": "real",
                    "reference_transition": str(reference_result.output_dir),
                    "job_name": "flow_eval_job",
                    "notes": "analyzer selected generated-glitch because intent mentioned glitch",
                    "analysis": {"style_reason": "intent mentioned glitch"},
                },
            ),
            patch(
                "overlay_harness.cli.build_transition_analysis_artifact",
                return_value={
                    "artifact_type": "transition_analysis",
                    "artifact_version": 2,
                    "sources": {
                        "source_a": "harness/examples/inputs/source_a_real",
                        "source_b": "harness/examples/inputs/source_b_real",
                        "reference_transition": str(reference_result.output_dir),
                    },
                    "facts": {
                        "resolved": {
                            "style_hint": "generated-glitch",
                            "input_kind": "real",
                            "job_name": "flow_eval_job",
                        },
                        "analyzer_inputs": {"flow": True},
                        "analysis_mode": "deterministic_rules",
                        "analysis_provider_runtime": {
                            "requested": {"kind": "deterministic_rules", "mode": "deterministic"},
                            "selected": {
                                "kind": "deterministic_rules",
                                "name": "deterministic_rules_v1",
                                "mode": "deterministic",
                            },
                            "adapter": {
                                "kind": "deterministic_rules",
                                "name": "deterministic_rules_v1",
                                "mode": "deterministic",
                                "status": "deterministic_adapter",
                            },
                            "configuration": {
                                "loaded": True,
                                "model_backed_enabled": False,
                            },
                            "execution": {
                                "entry_point": "overlay_harness.analyzer.analyze_transition",
                                "contract_type": "transition_analysis_model_execution",
                                "contract_version": 1,
                                "implementation_status": "ready",
                                "execution_mode": "builtin_deterministic",
                                "model_execution_contract": {
                                    "contract_type": "transition_analysis_model_execution",
                                    "contract_version": 1,
                                    "request_contract": {},
                                    "result_contract": {},
                                },
                                "input_contract": {},
                                "output_contract": {},
                            },
                        },
                        "transition_summary": {"combined_motion_level": "high"},
                        "transition_window": {
                            "frame_count": 30,
                            "detected_start_frame": 0,
                            "detected_end_frame": 29,
                            "detected_frame_count": 30,
                            "message": "prepared",
                        },
                        "transition_progression": {
                            "window_span_frames": 30,
                            "window_midpoint_frame": 14,
                            "window_coverage_ratio": 1.0,
                            "window_start_progress": 0.0,
                            "window_end_progress": 1.0,
                            "window_message": "prepared",
                        },
                    },
                    "planning_recommendation": {
                        **planning,
                        "analysis_engine": "deterministic_rules_v1",
                    },
                },
            ),
            patch("overlay_harness.cli.resolve_planned_frame_count", return_value=(30, "reference_transition_manifest")),
            patch("overlay_harness.cli.build_planned_job", return_value=(job, {"effect": "spec"})),
            patch("overlay_harness.cli.validate_job", return_value=SimpleNamespace(is_valid=True, issues=[])),
            patch("overlay_harness.cli._execute_job_command", return_value=run_result),
            patch("builtins.print") as print_mock,
        ):
            exit_code = _handle_flow(args, HARNESS_ROOT.parent, HARNESS_ROOT, HARNESS_ROOT / "configs", None)

        self.assertEqual(exit_code, 0)
        report_files = list(output_root.glob("transition_flow_*/flow_report.json"))
        self.assertEqual(len(report_files), 1)
        with report_files[0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["data"]["run"]["status"], "succeeded")
        self.assertEqual(payload["data"]["run"]["evaluation"]["overall_status"], "succeeded_with_score")
        self.assertEqual(payload["data"]["run"]["evaluation"]["score"]["status"], "succeeded")
        self.assertIsNone(payload["data"]["run"]["evaluation"]["score"]["error"])
        self.assertEqual(payload["data"]["analysis_model_execution_contract"]["contract_type"], "transition_analysis_model_execution")
        self.assertEqual(payload["data"]["analysis_model_execution_contract"]["contract_version"], 1)
        self.assertEqual(payload["data"]["analysis_provider_request"]["kind"], "deterministic_rules")
        self.assertEqual(payload["data"]["analysis_provider_resolution"]["resolved"]["kind"], "deterministic_rules")
        self.assertEqual(payload["data"]["analysis_provider_configuration"], None)
        self.assertEqual(payload["data"]["analysis_provider_runtime"]["execution"]["execution_mode"], "builtin_deterministic")
        self.assertEqual(payload["data"]["analysis_provider_adapter"]["status"], "deterministic_adapter")
        self.assertEqual(payload["data"]["analysis_provider_runtime"]["delegation"]["path"], "deterministic")
        stdout_payload = json.loads(print_mock.call_args.args[0])
        self.assertEqual(stdout_payload["status"], "succeeded")
        self.assertEqual(stdout_payload["analysis_artifact"]["planning_recommendation"]["analysis_engine"], "deterministic_rules_v1")
        self.assertEqual(stdout_payload["workspace_paths"]["similarity_report_file"], str(run_result["evaluation"]["score"]["report_file"]))
        self.assertEqual(stdout_payload["analysis_provider_request"]["kind"], "deterministic_rules")
        self.assertEqual(stdout_payload["analysis_provider_resolution"]["resolved"]["kind"], "deterministic_rules")

    def test_run_command_records_demo_video_artifact(self) -> None:
        job = self._build_job(reference_transition=self.root / "reference", frame_count=3)
        job.inputs.reference_transition = None
        job_path = self.root / "job.json"
        with job_path.open("w", encoding="utf-8") as handle:
            json.dump(job.to_dict(), handle)
            handle.write("\n")

        workspace_root = self.root / "workspace"
        workspace = SimpleNamespace(
            root=workspace_root,
            inputs_dir=workspace_root / "inputs",
            render_dir=workspace_root / "render",
            reports_dir=workspace_root / "reports",
            artifacts_dir=workspace_root / "artifacts",
        )
        for path in (workspace.inputs_dir, workspace.render_dir, workspace.reports_dir, workspace.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)

        invocation = SimpleNamespace(
            renderer_executable="renderer.exe",
            request_file=workspace.render_dir / "render_request.json",
            expected_output_dir=workspace.artifacts_dir,
            result_file=workspace.render_dir / "renderer_result.json",
            status="succeeded",
            message="renderer completed successfully",
            exit_code=0,
            stdout="",
            stderr="",
            produced_frame_count=3,
            expected_frame_count=3,
            output_check_message="renderer produced 3 expected PNG frames",
            renderer_result={"status": "ok"},
            demo_video_file=None,
            demo_video_status="not_attempted",
            demo_video_message="",
            demo_video_exit_code=None,
        )
        demo_video_file = workspace.artifacts_dir / "rendered.mp4"
        demo_video_result = {
            "status": "succeeded",
            "message": "encoded demo MP4 at rendered.mp4",
            "output_file": str(demo_video_file),
            "frame_count": 3,
            "ffmpeg_executable": "ffmpeg",
            "exit_code": 0,
        }

        with (
            patch("overlay_harness.cli.create_job_workspace", return_value=workspace),
            patch("overlay_harness.cli.prepare_render_invocation", return_value=invocation),
            patch("overlay_harness.cli.validate_job", return_value=SimpleNamespace(is_valid=True, issues=[])),
            patch("overlay_harness.cli.encode_render_demo_video", return_value=demo_video_result),
        ):
            result = _execute_job_command(
                repo_root=HARNESS_ROOT.parent,
                harness_root=HARNESS_ROOT,
                config_dir=HARNESS_ROOT / "configs",
                job_path=job_path,
                command_name="run",
                renderer="renderer.exe",
            )

        self.assertEqual(result["demo_video_file"], str(demo_video_file))
        self.assertEqual(result["demo_video_result"]["status"], "succeeded")
        self.assertEqual(result["workspace"], str(workspace_root))
        self.assertEqual(result["render_dir"], str(workspace.render_dir))
        self.assertEqual(result["reports_dir"], str(workspace.reports_dir))
        self.assertEqual(result["artifacts_dir"], str(workspace.artifacts_dir))

        report_path = Path(result["report"])
        with report_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["data"]["request_file"], str(workspace.render_dir / "render_request.json"))
        self.assertEqual(payload["data"]["renderer_result_file"], str(workspace.render_dir / "renderer_result.json"))
        self.assertEqual(payload["data"]["demo_video_file"], str(demo_video_file))
        self.assertEqual(payload["data"]["demo_video_status"], "succeeded")
        self.assertEqual(payload["data"]["demo_video_result"]["output_file"], str(demo_video_file))

    def test_sample_video_command_with_explicit_fx_id_copies_output_video(self) -> None:
        source_a = self.root / "source_a"
        source_b = self.root / "source_b"
        self._write_bmp_sequence(source_a, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_bmp_sequence(source_b, [(255, 255, 255), (255, 255, 255), (255, 255, 255)])

        output_video = self.root / "sample_reference.mp4"
        sample_demo_source = self.root / "sample_demo_source.mp4"
        sample_demo_source.write_bytes(b"demo-video")

        run_result = {
            "exit_code": 0,
            "workspace": str(self.root / "workspace"),
            "report": str(self.root / "workspace" / "reports" / "run_report.json"),
            "request_file": str(self.root / "workspace" / "render" / "render_request.json"),
            "renderer_result_file": str(self.root / "workspace" / "render" / "renderer_result.json"),
            "status": "succeeded",
            "summary": "renderer produced 30 expected PNG frames",
            "demo_video_file": str(sample_demo_source),
        }

        args = SimpleNamespace(
            source_a=source_a,
            source_b=source_b,
            output_video=output_video,
            fx_id="CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
            output_root=self.root / "sample_workspace",
            job_name=None,
            width=1920,
            height=1080,
            fps=30,
            frame_count=30,
            renderer=None,
            ffmpeg=None,
        )
        sample_workspace_root = self.root / "sample_workspace"
        before_reports = set(sample_workspace_root.glob("sample_video_*/sample_video_report.json"))

        with (
            patch("overlay_harness.cli._execute_job_command", return_value=run_result),
            patch("overlay_harness.cli.validate_job", return_value=SimpleNamespace(is_valid=True, issues=[])),
            patch("builtins.print") as print_mock,
        ):
            exit_code = _handle_sample_video(args, HARNESS_ROOT.parent, HARNESS_ROOT, HARNESS_ROOT / "configs", None)

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_video.exists())

        report_files = [path for path in sample_workspace_root.glob("sample_video_*/sample_video_report.json") if path not in before_reports]
        self.assertEqual(len(report_files), 1)
        with report_files[0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["data"]["workspace_paths"]["sample_root"], str(payload["data"]["sample_root"]))
        self.assertEqual(payload["data"]["workspace_paths"]["demo_video_file"], str(sample_demo_source))
        self.assertEqual(payload["data"]["workspace_paths"]["render_request_file"], str(run_result["request_file"]))
        self.assertEqual(payload["data"]["workspace_paths"]["renderer_result_file"], str(run_result["renderer_result_file"]))
        self.assertEqual(payload["data"]["selected_fx_id"], "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC")
        self.assertEqual(payload["data"]["output_video"], str(output_video))
        self.assertEqual(payload["data"]["sample_context"]["input_source"], "prepared_sources")
        self.assertEqual(payload["data"]["sample_context"]["source_a"], source_a.relative_to(HARNESS_ROOT.parent).as_posix())
        self.assertEqual(payload["data"]["sample_context"]["source_b"], source_b.relative_to(HARNESS_ROOT.parent).as_posix())
        self.assertEqual(payload["data"]["sample_context"]["selected_fx_id"], "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC")
        self.assertEqual(output_video.read_bytes(), sample_demo_source.read_bytes())
        self.assertIn("sample_workspace", payload["data"]["sample_root"])
        stdout_payload = json.loads(print_mock.call_args.args[0])
        self.assertEqual(stdout_payload["workspace_paths"]["sample_root"], str(payload["data"]["sample_root"]))
        self.assertEqual(stdout_payload["workspace_paths"]["demo_video_file"], str(sample_demo_source))
        self.assertIn("render_request_file", stdout_payload["workspace_paths"])
        self.assertEqual(stdout_payload["workspace_paths"]["renderer_result_file"], str(run_result["renderer_result_file"]))
        self.assertEqual(stdout_payload["sample_context"]["input_source"], "prepared_sources")
        self.assertEqual(stdout_payload["sample_context"]["source_a"], source_a.relative_to(HARNESS_ROOT.parent).as_posix())
        self.assertEqual(stdout_payload["sample_context"]["source_b"], source_b.relative_to(HARNESS_ROOT.parent).as_posix())

    def test_sample_video_command_with_force_mode_uses_forced_planner_mode(self) -> None:
        source_a = self.root / "source_a"
        source_b = self.root / "source_b"
        self._write_bmp_sequence(source_a, [(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self._write_bmp_sequence(source_b, [(255, 255, 255), (255, 255, 255), (255, 255, 255)])

        output_video = self.root / "forced_glitch.mp4"
        sample_demo_source = self.root / "sample_demo_source_force.mp4"
        sample_demo_source.write_bytes(b"demo-video-force")

        run_result = {
            "exit_code": 0,
            "workspace": str(self.root / "workspace_force"),
            "report": str(self.root / "workspace_force" / "reports" / "run_report.json"),
            "status": "succeeded",
            "summary": "renderer produced 30 expected PNG frames",
            "demo_video_file": str(sample_demo_source),
        }

        args = SimpleNamespace(
            source_a=source_a,
            source_b=source_b,
            output_video=output_video,
            fx_id=None,
            style=None,
            force_mode="builtin-glitch",
            output_root=self.root / "sample_workspace",
            job_name=None,
            width=1920,
            height=1080,
            fps=30,
            frame_count=30,
            renderer=None,
            ffmpeg=None,
        )
        sample_workspace_root = self.root / "sample_workspace"
        before_reports = set(sample_workspace_root.glob("sample_video_*/sample_video_report.json"))

        with (
            patch("overlay_harness.cli._execute_job_command", return_value=run_result),
            patch("overlay_harness.cli.validate_job", return_value=SimpleNamespace(is_valid=True, issues=[])),
            patch("builtins.print") as print_mock,
        ):
            exit_code = _handle_sample_video(args, HARNESS_ROOT.parent, HARNESS_ROOT, HARNESS_ROOT / "configs", None)

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_video.exists())

        report_files = [path for path in sample_workspace_root.glob("sample_video_*/sample_video_report.json") if path not in before_reports]
        self.assertEqual(len(report_files), 1)
        with report_files[0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["data"]["planning"]["mode"], "builtin-glitch")
        self.assertEqual(payload["data"]["workspace_paths"]["sample_root"], str(payload["data"]["sample_root"]))
        self.assertEqual(payload["data"]["selected_fx_id"], "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4")
        self.assertEqual(output_video.read_bytes(), sample_demo_source.read_bytes())
        stdout_payload = json.loads(print_mock.call_args.args[0])
        self.assertEqual(stdout_payload["workspace_paths"]["sample_root"], str(payload["data"]["sample_root"]))

    def test_render_job_preserves_planning_metadata(self) -> None:
        job = self._build_job(reference_transition=self.root / "reference", frame_count=3)
        job.planning = {
            "auto": True,
            "retrieval": {
                "status": "retrieved",
                "effect_id": "builtin-glitch",
                "mode": "builtin-glitch",
            },
        }

        payload = job.to_dict()
        restored = RenderJob.from_dict(payload)

        self.assertEqual(payload["planning"]["retrieval"]["effect_id"], "builtin-glitch")
        self.assertIsNotNone(restored.planning)
        self.assertEqual(restored.planning["retrieval"]["mode"], "builtin-glitch")

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

    def test_validator_rejects_prepared_reference_dimension_mismatch(self) -> None:
        reference_dir = self.root / "reference"
        self._write_bmp_sequence(reference_dir, [(0, 0, 0), (64, 64, 64), (255, 255, 255)])
        self._write_reference_manifest(reference_dir, frame_count=3)

        validation = validate_job(
            self._build_job(reference_transition=reference_dir, frame_count=3, width=4, height=2),
            HARNESS_ROOT.parent,
            self._allowed_effects(),
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("dimensions do not match render width/height" in issue.message for issue in validation.issues)
        )

    def test_effect_catalog_prefers_builtin_for_generated_styles(self) -> None:
        catalog = build_effect_catalog(HARNESS_ROOT.parent)
        selected = select_effect_candidate(catalog, style="generated-glitch", input_kind="real")

        self.assertIsNotNone(selected)
        self.assertEqual(selected["mode"], "builtin-glitch")
        self.assertEqual(selected["effect_id"], "builtin-glitch")
        self.assertEqual(selected["match_kind"], "alias")
        self.assertEqual(selected["matched_style_hint"], "glitch")
        self.assertGreaterEqual(selected["candidate_count"], 1)

    def test_auto_plan_prefers_catalog_retrieval_for_generated_glitch(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="generated-glitch",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-glitch")
        self.assertEqual(preset, "real-smoke-glitch")

    def test_auto_plan_prefers_catalog_retrieval_for_glitch_04(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="glitch-04",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-glitch")
        self.assertEqual(preset, "real-smoke-glitch")

    def test_auto_plan_prefers_catalog_retrieval_for_ui_snapshot(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="ui-snapshot",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-ui-snapshot")
        self.assertIsNone(preset)

    def test_auto_plan_prefers_catalog_retrieval_for_slide_07(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="slide-07",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-seamless")
        self.assertEqual(preset, "real-smoke-seamless")

    def test_auto_plan_prefers_catalog_retrieval_for_glitch_distortion(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="glitch-distortion",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-glitch-distortion")
        self.assertEqual(preset, "real-smoke-glitch")

    def test_recommended_plan_includes_retrieval_summary(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        plan = build_recommended_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            hint_data={
                "style_hint": "generated-glitch",
                "input_kind": "real",
                "job_name": "test_job",
            },
        )

        self.assertEqual(plan["retrieval"]["status"], "retrieved")
        self.assertEqual(plan["retrieval"]["effect_id"], "builtin-glitch")
        self.assertEqual(plan["retrieval"]["mode"], "builtin-glitch")
        self.assertEqual(plan["retrieval"]["match_kind"], "alias")
        self.assertEqual(plan["retrieval"]["matched_style_hint"], "glitch")
        self.assertGreaterEqual(plan["retrieval"]["candidate_count"], 1)
        self.assertFalse(plan["retrieval"]["fallback_used"])

    def test_analyzer_prefers_generated_alias_styles_from_intent(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="generated rgb split transition",
            prefer_generated=False,
            reference_transition=None,
            job_name=None,
        )

        self.assertEqual(hint["style_hint"], "generated-rgb-split")
        self.assertEqual(hint["analysis_provider"]["kind"], "deterministic_rules")
        self.assertEqual(hint["analysis_provider"]["name"], "deterministic_rules_v1")

    def test_analyzer_uses_custom_provider_when_supplied(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        class CustomProvider:
            def analyze_transition(self, **kwargs):
                return {
                    "style_hint": "generated-noise",
                    "input_kind": kwargs["input_kind"],
                    "reference_transition": None,
                    "job_name": kwargs["job_name"],
                    "notes": "custom provider used",
                    "analysis": {"style_reason": "custom provider used"},
                }

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="generated noise transition",
            prefer_generated=False,
            reference_transition=None,
            job_name="custom_provider_job",
            provider=CustomProvider(),
        )

        self.assertEqual(hint["style_hint"], "generated-noise")
        self.assertEqual(hint["analysis_provider"]["kind"], "model_backed")
        self.assertEqual(hint["analysis_provider"]["name"], "custom_provider")

    def test_analyzer_model_backed_request_resolves_to_deterministic_fallback(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="generated glitch transition",
            prefer_generated=False,
            reference_transition=None,
            job_name="model_backed_request",
        )
        artifact = build_transition_analysis_artifact(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            analyzer_inputs={
                "analysis_provider_kind": "model_backed",
                "analysis_provider_name": "openai-transition-model",
                "analysis_provider_mode": "vision",
                "analysis_mode": "deterministic_rules",
                "analysis_provider_configuration": {
                    "config_path": str(HARNESS_ROOT / "configs" / "analysis_provider.json"),
                    "config_source": "repo:configs/analysis_provider.json",
                    "config_version": 1,
                    "model_backed_provider": {
                        "enabled": False,
                    },
                },
            },
            hint=hint,
        )

        self.assertEqual(artifact["facts"]["analysis_provider_request"]["kind"], "model_backed")
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["status"], "fallback_to_deterministic")
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["configuration"]["loaded"], True)
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["configuration"]["config_source"], "repo:configs/analysis_provider.json")
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["configuration"]["model_backed_enabled"], False)
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["execution"]["execution_mode"], "deterministic_fallback")
        self.assertEqual(artifact["facts"]["analysis_provider_runtime"]["execution"]["implementation_status"], "fallback_only")
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["resolved"]["kind"], "deterministic_rules")
        self.assertEqual(artifact["facts"]["analysis_provider_resolution"]["resolved"]["name"], "deterministic_rules_v1")

    def test_analyzer_uses_approved_alias_for_generated_glitch_intent(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="generated glitch transition",
            prefer_generated=False,
            reference_transition=None,
            job_name=None,
        )

        self.assertEqual(hint["style_hint"], "generated-noise")

    def test_analyzer_uses_approved_alias_for_generated_smooth_intent(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="generated seamless transition",
            prefer_generated=False,
            reference_transition=None,
            job_name=None,
        )

        self.assertEqual(hint["style_hint"], "generated-dissolve")

    def test_analyzer_prefers_generated_alias_when_requested(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        hint = analyze_transition(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            input_kind="auto",
            style_hint=None,
            intent="wipe transition",
            prefer_generated=True,
            reference_transition=None,
            job_name=None,
        )

        self.assertEqual(hint["style_hint"], "generated-wipe")

    def test_metadata_generated_preference_uses_approved_aliases(self) -> None:
        high_energy = derive_analyzer_inputs_from_metadata(
            {
                "motion_level": "high",
                "visual_energy": "high",
                "prefer_generated": True,
            }
        )
        default_generated = derive_analyzer_inputs_from_metadata(
            {
                "motion_level": "low",
                "visual_energy": "low",
                "prefer_generated": True,
            }
        )

        self.assertEqual(high_energy["style_hint"], "generated-noise")
        self.assertEqual(default_generated["style_hint"], "generated-rgb-split")

    def test_metadata_smooth_family_uses_approved_generated_alias(self) -> None:
        derived = derive_analyzer_inputs_from_metadata(
            {
                "transition_family": "smooth",
                "prefer_generated": True,
            }
        )

        self.assertEqual(derived["style_hint"], "generated-dissolve")

    def test_auto_plan_prefers_catalog_retrieval_for_generated_seamless(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        preset, mode, input_kind = resolve_auto_plan(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            style="generated-seamless",
            input_kind="auto",
        )

        self.assertEqual(input_kind, "real")
        self.assertEqual(mode, "builtin-seamless")
        self.assertEqual(preset, "real-smoke-seamless")

    def test_recommended_plan_marks_generated_seamless_placeholder_when_catalog_missing(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        plan = build_recommended_plan(
            repo_root=self.root,
            source_a=source_a,
            source_b=source_b,
            hint_data={
                "style_hint": "generated-seamless",
                "input_kind": "real",
                "job_name": "test_job",
            },
        )

        self.assertEqual(plan["mode"], "generated-seamless-placeholder")
        self.assertEqual(plan["retrieval"]["status"], "not_found")
        self.assertTrue(plan["retrieval"]["fallback_used"])
        self.assertEqual(plan["retrieval"]["fallback_mode"], "generated-seamless-placeholder")
        self.assertEqual(plan["retrieval"]["fallback_reason"], "effect catalog is unavailable")

    def test_recommended_plan_marks_placeholder_fallback_when_catalog_missing(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        plan = build_recommended_plan(
            repo_root=self.root,
            source_a=source_a,
            source_b=source_b,
            hint_data={
                "style_hint": "generated-glitch",
                "input_kind": "real",
                "job_name": "test_job",
            },
        )

        self.assertEqual(plan["mode"], "generated-glitch-placeholder")
        self.assertEqual(plan["retrieval"]["status"], "not_found")
        self.assertTrue(plan["retrieval"]["fallback_used"])
        self.assertEqual(plan["retrieval"]["fallback_mode"], "generated-glitch-placeholder")
        self.assertEqual(plan["retrieval"]["fallback_reason"], "effect catalog is unavailable")

    def test_explicit_generated_placeholder_mode_includes_fallback_metadata(self) -> None:
        source_a = self.root / "source_a"
        source_b = self.root / "source_b"
        source_a.mkdir(parents=True, exist_ok=True)
        source_b.mkdir(parents=True, exist_ok=True)

        job, _effect_spec_payload = build_planned_job(
            repo_root=HARNESS_ROOT.parent,
            source_a=source_a,
            source_b=source_b,
            mode="generated-glitch-placeholder",
            width=1920,
            height=1080,
            fps=30,
            frame_count=30,
            output_format="png_sequence",
            job_name="explicit_placeholder",
            reference_transition=None,
            effect_spec_output=None,
            planning={
                "auto": False,
                "mode": "generated-glitch-placeholder",
                "preset": "real-smoke-generated-glitch",
                "job_name": "explicit_placeholder",
            },
        )

        self.assertIsNotNone(job.planning)
        self.assertEqual(job.planning["mode"], "generated-glitch-placeholder")
        self.assertEqual(job.planning["retrieval"]["status"], "not_found")
        self.assertTrue(job.planning["retrieval"]["fallback_used"])
        self.assertEqual(job.planning["retrieval"]["fallback_mode"], "generated-glitch-placeholder")
        self.assertEqual(job.planning["retrieval"]["fallback_preset"], "real-smoke-generated-glitch")
        self.assertEqual(
            job.planning["retrieval"]["fallback_reason"],
            "generated-placeholder mode was requested explicitly",
        )

    def test_index_effects_writes_catalog(self) -> None:
        Args = type(
            "Args",
            (),
            {
                "output": str(self.root / "effect_catalog.json"),
                "source_manifest": None,
            },
        )

        exit_code = _handle_index_effects(Args(), HARNESS_ROOT.parent)
        self.assertEqual(exit_code, 0)

        catalog = load_effect_catalog(self.root / "effect_catalog.json")
        self.assertEqual(catalog["catalog_type"], "effect_catalog")
        self.assertEqual(catalog["catalog_version"], 1)
        self.assertEqual(catalog["source_manifest"], "harness/configs/effect_catalog_sources.json")
        self.assertEqual(len(catalog["source_manifest_sha256"]), 64)
        self.assertEqual(catalog["registration_count"], len(catalog["effects"]))
        self.assertGreaterEqual(len(catalog["effects"]), 24)

    def test_index_effects_uses_custom_source_manifest(self) -> None:
        source_manifest = self.root / "effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "custom-builtin-seamless",
                            "mode": "builtin-seamless",
                            "effect_source": "builtin",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/effect_specs/builtin_seamless_sliding.json"],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        Args = type(
            "Args",
            (),
            {
                "output": str(self.root / "custom_effect_catalog.json"),
                "source_manifest": str(source_manifest),
            },
        )

        exit_code = _handle_index_effects(Args(), HARNESS_ROOT.parent)
        self.assertEqual(exit_code, 0)

        catalog = load_effect_catalog(self.root / "custom_effect_catalog.json")
        self.assertEqual(catalog["effects"][0]["effect_id"], "custom-builtin-seamless")
        self.assertEqual(catalog["retrieval_index"]["seamless"], "custom-builtin-seamless")
        self.assertEqual(catalog["source_manifest"], source_manifest.relative_to(HARNESS_ROOT.parent).as_posix())
        self.assertEqual(len(catalog["source_manifest_sha256"]), 64)

    def test_effect_catalog_source_manifest_is_loaded(self) -> None:
        catalog = build_effect_catalog(HARNESS_ROOT.parent)

        self.assertEqual(catalog["catalog_type"], "effect_catalog")
        self.assertEqual(catalog["catalog_version"], 1)
        self.assertEqual(catalog["source_root"], "harness")
        self.assertEqual(catalog["source_manifest"], "harness/configs/effect_catalog_sources.json")
        self.assertGreaterEqual(len(catalog["effects"]), 24)
        self.assertEqual(catalog["retrieval_index"]["glitch"], "builtin-glitch")
        self.assertEqual(catalog["retrieval_index"]["blur"], "builtin-blur")
        self.assertEqual(catalog["retrieval_index"]["blur-upgrow"], "builtin-blur-upgrow")
        self.assertEqual(catalog["retrieval_index"]["blur-shakezoom"], "builtin-blur-shakezoom")
        self.assertEqual(catalog["retrieval_index"]["blur-diagblur"], "builtin-blur-diagblur")
        self.assertEqual(catalog["retrieval_index"]["blur-hexbokeh"], "builtin-blur-hexbokeh")
        self.assertEqual(catalog["retrieval_index"]["blur-diamondbokeh"], "builtin-blur-diamondbokeh")
        self.assertEqual(catalog["retrieval_index"]["blur-fadeblur"], "builtin-blur-fadeblur")
        self.assertEqual(catalog["retrieval_index"]["blur-rotateblur"], "builtin-blur-rotateblur")
        self.assertEqual(catalog["retrieval_index"]["blur-dimfade"], "builtin-blur-dimfade")
        self.assertEqual(catalog["retrieval_index"]["ui"], "builtin-ui-snapshot")
        self.assertEqual(catalog["retrieval_index"]["ui-app-swipe"], "builtin-ui-app-swipe")
        self.assertEqual(catalog["retrieval_index"]["ui-rotate-face"], "builtin-ui-rotate-face")
        self.assertEqual(catalog["retrieval_index"]["glitch-hdistortion"], "builtin-glitch-hdistortion")
        self.assertEqual(catalog["retrieval_index"]["glitch-hdistortion2"], "builtin-glitch-hdistortion2")
        self.assertEqual(catalog["retrieval_index"]["glitch-stretch-swipe"], "builtin-glitch-stretch-swipe")
        self.assertEqual(catalog["retrieval_index"]["glitch-tunewave"], "builtin-glitch-tunewave")
        self.assertEqual(catalog["retrieval_index"]["distortion"], "builtin-glitch-distortion")
        self.assertEqual(len(catalog["source_manifest_sha256"]), 64)
        builtin_seamless = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-seamless")
        builtin_glitch = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch")
        builtin_camcorder = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-camcorder")
        builtin_particle = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-particle-spray"
        )
        builtin_frame_overlay = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-frame-overlay"
        )
        builtin_blur = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur")
        builtin_blur_upgrow = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-upgrow")
        builtin_blur_shakezoom = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-shakezoom"
        )
        builtin_blur_diagblur = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-diagblur"
        )
        builtin_blur_hexbokeh = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-hexbokeh")
        builtin_blur_diamondbokeh = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-diamondbokeh"
        )
        builtin_blur_fadeblur = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-fadeblur")
        builtin_blur_rotateblur = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-rotateblur"
        )
        builtin_blur_dimfade = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-blur-dimfade")
        builtin_ui_snapshot = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-ui-snapshot"
        )
        builtin_ui_app_swipe = next(effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-ui-app-swipe")
        builtin_ui_rotate_face = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-ui-rotate-face"
        )
        builtin_glitch_hdistortion = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch-hdistortion"
        )
        builtin_glitch_hdistortion2 = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch-hdistortion2"
        )
        builtin_glitch_stretch_swipe = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch-stretch-swipe"
        )
        builtin_glitch_tunewave = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch-tunewave"
        )
        builtin_glitch_distortion = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "builtin-glitch-distortion"
        )
        generated_seamless = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "generated-seamless-placeholder"
        )
        generated_glitch = next(
            effect for effect in catalog["effects"] if effect["effect_id"] == "generated-glitch-placeholder"
        )
        self.assertEqual(catalog["retrieval_index"]["camera"], "builtin-camcorder")
        self.assertEqual(catalog["retrieval_index"]["particle"], "builtin-particle-spray")
        self.assertEqual(catalog["retrieval_index"]["frame-overlay"], "builtin-frame-overlay")
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrSeamlessSliding.cpp", builtin_seamless["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrGlitchInfoManager.cpp", builtin_glitch["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrCamcorder.cpp", builtin_camcorder["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrParticleSprayInfoManager.cpp", builtin_particle["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrFrameOverlay.cpp", builtin_frame_overlay["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_upgrow["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_shakezoom["source_documents"])
        self.assertEqual(generated_seamless["style_hints"], ["generated-seamless"])
        self.assertEqual(generated_glitch["style_hints"], ["generated-glitch"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_diagblur["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_hexbokeh["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_diamondbokeh["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_fadeblur["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_rotateblur["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_blur_dimfade["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp", builtin_ui_snapshot["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp", builtin_ui_app_swipe["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.cpp", builtin_ui_rotate_face["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_glitch_hdistortion["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_glitch_hdistortion2["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_glitch_stretch_swipe["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_glitch_tunewave["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrAsWindLib.h", builtin_glitch_distortion["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrSeamlessSliding.cpp", generated_seamless["source_documents"])
        self.assertIn("overlaytrengine/OverlayTrPlugInFx/TrGlitchInfoManager.cpp", generated_glitch["source_documents"])

    def test_effect_catalog_source_manifest_covers_fxinfo_mappings(self) -> None:
        fxinfo_path = HARNESS_ROOT.parent / "overlaytrengine" / "OverlayTrPlugInFx" / "FxInfo.h"
        fxinfo_content = fxinfo_path.read_text(encoding="utf-8")
        fxinfo_fx_ids = {
            match.replace("\\\\", "\\")
            for match in re.findall(r'^\s*"([^"]+)"\s*,\s*"[^"]+"\s*,\s*\d+\s*$', fxinfo_content, re.MULTILINE)
        }

        source_manifest = json.loads((HARNESS_ROOT / "configs" / "effect_catalog_sources.json").read_text(encoding="utf-8"))
        builtin_registrations = [
            registration for registration in source_manifest["registrations"] if registration["effect_source"] == "builtin"
        ]
        generated_registrations = [
            registration
            for registration in source_manifest["registrations"]
            if registration["effect_source"] == "generated"
        ]

        self.assertEqual({registration["fx_id"] for registration in builtin_registrations}, fxinfo_fx_ids)
        self.assertEqual(
            {registration["effect_id"] for registration in generated_registrations},
            {"generated-seamless-placeholder", "generated-glitch-placeholder"},
        )

    def test_planner_styles_match_builtin_source_manifest_aliases(self) -> None:
        source_manifest = json.loads((HARNESS_ROOT / "configs" / "effect_catalog_sources.json").read_text(encoding="utf-8"))
        builtin_style_hints = {
            style
            for registration in source_manifest["registrations"]
            if registration["effect_source"] == "builtin"
            for style in registration["style_hints"]
        }

        auto_style_set = set(auto_styles())
        self.assertTrue(builtin_style_hints.issubset(auto_style_set))
        self.assertTrue(set(GENERATED_EFFECT_SUPPORTED_STYLES).issubset(auto_style_set))
        self.assertTrue(set(GENERATED_EFFECT_STYLES).issubset(auto_style_set))
        self.assertTrue(set(GENERATED_EFFECT_STYLE_ALIASES).issubset(auto_style_set))
        self.assertEqual(set(GENERATED_EFFECT_PLACEHOLDER_MODES), {"generated-seamless-placeholder", "generated-glitch-placeholder"})
        self.assertIn("generated-seamless", auto_style_set)
        self.assertIn("generated-glitch", auto_style_set)

    def test_auto_plan_resolves_generated_grammar_styles(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        expected_modes = {
            "wipe": ("generated-seamless-placeholder", "real-smoke-seamless"),
            "dissolve": ("generated-seamless-placeholder", "real-smoke-seamless"),
            "mask": ("generated-seamless-placeholder", "real-smoke-seamless"),
            "uv-shift": ("generated-glitch-placeholder", "real-smoke-glitch"),
            "feathering": ("generated-seamless-placeholder", "real-smoke-seamless"),
            "rgb-split": ("generated-glitch-placeholder", "real-smoke-glitch"),
            "noise": ("generated-glitch-placeholder", "real-smoke-glitch"),
        }

        for style, (expected_mode, expected_preset) in expected_modes.items():
            with self.subTest(style=style):
                preset, mode, input_kind = resolve_auto_plan(
                    repo_root=HARNESS_ROOT.parent,
                    source_a=source_a,
                    source_b=source_b,
                    style=style,
                    input_kind="auto",
                )

                self.assertEqual(input_kind, "real")
                self.assertEqual(mode, expected_mode)
                self.assertEqual(preset, expected_preset)

    def test_auto_plan_resolves_generated_grammar_alias_styles(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        expected_modes = {
            "generated-wipe": ("generated-seamless-placeholder", "real-smoke-seamless"),
            "generated-uv-shift": ("generated-glitch-placeholder", "real-smoke-glitch"),
            "generated-rgb-split": ("generated-glitch-placeholder", "real-smoke-glitch"),
        }

        for style, (expected_mode, expected_preset) in expected_modes.items():
            with self.subTest(style=style):
                preset, mode, input_kind = resolve_auto_plan(
                    repo_root=HARNESS_ROOT.parent,
                    source_a=source_a,
                    source_b=source_b,
                    style=style,
                    input_kind="auto",
                )

                self.assertEqual(input_kind, "real")
                self.assertEqual(mode, expected_mode)
                self.assertEqual(preset, expected_preset)

    def test_resolve_auto_plan_rejects_unsupported_generated_style(self) -> None:
        source_a = HARNESS_ROOT.parent / "harness/examples/inputs/source_a_real"
        source_b = HARNESS_ROOT.parent / "harness/examples/inputs/source_b_real"

        with self.assertRaisesRegex(ValueError, "unsupported style 'generated-warp'"):
            resolve_auto_plan(
                repo_root=HARNESS_ROOT.parent,
                source_a=source_a,
                source_b=source_b,
                style="generated-warp",
                input_kind="auto",
            )

    def test_effect_catalog_selects_additional_builtin_families(self) -> None:
        catalog = build_effect_catalog(HARNESS_ROOT.parent)

        blur = select_effect_candidate(catalog, style="blur", input_kind="real")
        blur_upgrow = select_effect_candidate(catalog, style="blur-upgrow", input_kind="real")
        blur_shakezoom = select_effect_candidate(catalog, style="blur-shakezoom", input_kind="real")
        blur_diagblur = select_effect_candidate(catalog, style="blur-diagblur", input_kind="real")
        blur_hexbokeh = select_effect_candidate(catalog, style="blur-hexbokeh", input_kind="real")
        blur_diamondbokeh = select_effect_candidate(catalog, style="blur-diamondbokeh", input_kind="real")
        blur_fadeblur = select_effect_candidate(catalog, style="blur-fadeblur", input_kind="real")
        blur_rotateblur = select_effect_candidate(catalog, style="blur-rotateblur", input_kind="real")
        blur_dimfade = select_effect_candidate(catalog, style="blur-dimfade", input_kind="real")
        blur_dollarbokeh = select_effect_candidate(catalog, style="blur-dollarbokeh", input_kind="real")
        ui = select_effect_candidate(catalog, style="ui", input_kind="real")
        ui_app_swipe = select_effect_candidate(catalog, style="ui-app-swipe", input_kind="real")
        ui_rotate_face = select_effect_candidate(catalog, style="ui-rotate-face", input_kind="real")
        ui_snapshot = select_effect_candidate(catalog, style="ui-snapshot", input_kind="real")
        glitch_hdistortion = select_effect_candidate(catalog, style="glitch-hdistortion", input_kind="real")
        glitch_hdistor1 = select_effect_candidate(catalog, style="glitch-hdistor1", input_kind="real")
        glitch_hdistortion2 = select_effect_candidate(catalog, style="glitch-hdistortion2", input_kind="real")
        glitch_stretch_swipe = select_effect_candidate(catalog, style="glitch-stretch-swipe", input_kind="real")
        glitch_tunewave = select_effect_candidate(catalog, style="glitch-tunewave", input_kind="real")
        slide_07 = select_effect_candidate(catalog, style="slide-07", input_kind="real")
        camera_02 = select_effect_candidate(catalog, style="camera-02", input_kind="real")
        sparkle_01 = select_effect_candidate(catalog, style="sparkle-01", input_kind="real")
        film_roll_01 = select_effect_candidate(catalog, style="film-roll-01", input_kind="real")
        glitch_04 = select_effect_candidate(catalog, style="glitch-04", input_kind="real")
        glitch_distortion = select_effect_candidate(catalog, style="glitch-distortion", input_kind="real")
        distortion = select_effect_candidate(catalog, style="distortion", input_kind="real")
        camcorder = select_effect_candidate(catalog, style="camcorder", input_kind="real")
        particle = select_effect_candidate(catalog, style="particle", input_kind="real")
        frame_overlay = select_effect_candidate(catalog, style="frame-overlay", input_kind="real")

        self.assertIsNotNone(blur)
        self.assertIsNotNone(blur_upgrow)
        self.assertIsNotNone(blur_shakezoom)
        self.assertIsNotNone(blur_diagblur)
        self.assertIsNotNone(blur_hexbokeh)
        self.assertIsNotNone(blur_diamondbokeh)
        self.assertIsNotNone(blur_fadeblur)
        self.assertIsNotNone(blur_rotateblur)
        self.assertIsNotNone(blur_dimfade)
        self.assertIsNotNone(blur_dollarbokeh)
        self.assertIsNotNone(ui)
        self.assertIsNotNone(ui_app_swipe)
        self.assertIsNotNone(ui_rotate_face)
        self.assertIsNotNone(ui_snapshot)
        self.assertIsNotNone(glitch_hdistortion)
        self.assertIsNotNone(glitch_hdistor1)
        self.assertIsNotNone(glitch_hdistortion2)
        self.assertIsNotNone(glitch_stretch_swipe)
        self.assertIsNotNone(glitch_tunewave)
        self.assertIsNotNone(slide_07)
        self.assertIsNotNone(camera_02)
        self.assertIsNotNone(sparkle_01)
        self.assertIsNotNone(film_roll_01)
        self.assertIsNotNone(glitch_04)
        self.assertIsNotNone(glitch_distortion)
        self.assertIsNotNone(distortion)
        self.assertIsNotNone(camcorder)
        self.assertIsNotNone(particle)
        self.assertIsNotNone(frame_overlay)
        self.assertEqual(blur["effect_id"], "builtin-blur")
        self.assertEqual(blur_upgrow["effect_id"], "builtin-blur-upgrow")
        self.assertEqual(blur_shakezoom["effect_id"], "builtin-blur-shakezoom")
        self.assertEqual(blur_diagblur["effect_id"], "builtin-blur-diagblur")
        self.assertEqual(blur_hexbokeh["effect_id"], "builtin-blur-hexbokeh")
        self.assertEqual(blur_diamondbokeh["effect_id"], "builtin-blur-diamondbokeh")
        self.assertEqual(blur_fadeblur["effect_id"], "builtin-blur-fadeblur")
        self.assertEqual(blur_rotateblur["effect_id"], "builtin-blur-rotateblur")
        self.assertEqual(blur_dimfade["effect_id"], "builtin-blur-dimfade")
        self.assertEqual(blur_dollarbokeh["effect_id"], "builtin-blur")
        self.assertEqual(ui["effect_id"], "builtin-ui-snapshot")
        self.assertEqual(ui_app_swipe["effect_id"], "builtin-ui-app-swipe")
        self.assertEqual(ui_rotate_face["effect_id"], "builtin-ui-rotate-face")
        self.assertEqual(ui_snapshot["effect_id"], "builtin-ui-snapshot")
        self.assertEqual(glitch_hdistortion["effect_id"], "builtin-glitch-hdistortion")
        self.assertEqual(glitch_hdistor1["effect_id"], "builtin-glitch-hdistortion")
        self.assertEqual(glitch_hdistortion2["effect_id"], "builtin-glitch-hdistortion2")
        self.assertEqual(glitch_stretch_swipe["effect_id"], "builtin-glitch-stretch-swipe")
        self.assertEqual(glitch_tunewave["effect_id"], "builtin-glitch-tunewave")
        self.assertEqual(slide_07["effect_id"], "builtin-seamless")
        self.assertEqual(camera_02["effect_id"], "builtin-camcorder")
        self.assertEqual(sparkle_01["effect_id"], "builtin-particle-spray")
        self.assertEqual(film_roll_01["effect_id"], "builtin-frame-overlay")
        self.assertEqual(glitch_04["effect_id"], "builtin-glitch")
        self.assertEqual(glitch_distortion["effect_id"], "builtin-glitch-distortion")
        self.assertEqual(distortion["effect_id"], "builtin-glitch-distortion")
        self.assertEqual(camcorder["effect_id"], "builtin-camcorder")
        self.assertEqual(particle["effect_id"], "builtin-particle-spray")
        self.assertEqual(frame_overlay["effect_id"], "builtin-frame-overlay")
        self.assertEqual(blur_hexbokeh["match_kind"], "exact")
        self.assertEqual(blur_dollarbokeh["match_kind"], "exact")
        self.assertEqual(ui_snapshot["match_kind"], "exact")
        self.assertEqual(glitch_hdistor1["match_kind"], "exact")
        self.assertEqual(glitch_tunewave["match_kind"], "exact")
        self.assertEqual(slide_07["match_kind"], "alias")
        self.assertEqual(camera_02["match_kind"], "alias")
        self.assertEqual(sparkle_01["match_kind"], "alias")
        self.assertEqual(film_roll_01["match_kind"], "alias")
        self.assertEqual(glitch_04["match_kind"], "alias")
        self.assertEqual(glitch_distortion["match_kind"], "alias")

    def test_effect_catalog_prefers_earlier_source_alias_when_priority_ties(self) -> None:
        catalog = {
            "effects": [
                {
                    "effect_id": "builtin-z-last",
                    "effect_source": "builtin",
                    "family": "demo",
                    "fx_id": "fx-z",
                    "mode": "builtin-z-last",
                    "retrieval_priority": 0,
                    "source_documents": ["demo/z.json"],
                    "style_hints": ["alpha", "shared"],
                },
                {
                    "effect_id": "builtin-a-first",
                    "effect_source": "builtin",
                    "family": "demo",
                    "fx_id": "fx-a",
                    "mode": "builtin-a-first",
                    "retrieval_priority": 0,
                    "source_documents": ["demo/a.json"],
                    "style_hints": ["beta", "gamma", "shared"],
                },
            ]
        }

        selected = select_effect_candidate(catalog, style="shared", input_kind="real")

        self.assertIsNotNone(selected)
        self.assertEqual(selected["effect_id"], "builtin-z-last")
        self.assertEqual(selected["match_kind"], "alias")
        self.assertEqual(selected["candidate_count"], 2)

    def test_effect_catalog_source_manifest_rejects_duplicate_effect_ids(self) -> None:
        source_manifest = self.root / "duplicate_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "duplicate-effect",
                            "mode": "builtin-seamless",
                            "effect_source": "builtin",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/effect_specs/builtin_seamless_sliding.json"],
                        },
                        {
                            "effect_id": "duplicate-effect",
                            "mode": "builtin-glitch",
                            "effect_source": "builtin",
                            "family": "glitch",
                            "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
                            "style_hints": ["glitch"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/render_job.effect_spec.sample.json"],
                        },
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "duplicate effect_id"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_source_manifest_rejects_missing_source_documents(self) -> None:
        source_manifest = self.root / "missing_source_document_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "missing-doc-effect",
                            "mode": "builtin-seamless",
                            "effect_source": "builtin",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/effect_specs/does_not_exist.json"],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "references missing source document"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_source_manifest_rejects_invalid_effect_source(self) -> None:
        source_manifest = self.root / "invalid_source_type_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "invalid-source-effect",
                            "mode": "builtin-seamless",
                            "effect_source": "experimental",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/effect_specs/builtin_seamless_sliding.json"],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "must use effect_source builtin or generated"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_source_manifest_rejects_generated_without_fallback(self) -> None:
        source_manifest = self.root / "generated_without_fallback_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "generated-missing-fallback",
                            "mode": "generated-glitch-placeholder",
                            "effect_source": "generated",
                            "family": "glitch",
                            "fx_id": "CES_PlugIn_Glitch.dll\\DSP_TR_04_Bad Signal_4",
                            "style_hints": ["generated-glitch"],
                            "retrieval_priority": 10,
                            "source_documents": ["harness/examples/effect_specs/generated_glitch_placeholder.json"],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "must include fallback_fx_id for generated entries"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_source_manifest_rejects_builtin_with_fallback(self) -> None:
        source_manifest = self.root / "builtin_with_fallback_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "builtin-with-fallback",
                            "mode": "builtin-seamless",
                            "effect_source": "builtin",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "fallback_fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": ["harness/examples/effect_specs/builtin_seamless_sliding.json"],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "must not include fallback_fx_id for builtin entries"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_source_manifest_rejects_empty_source_documents(self) -> None:
        source_manifest = self.root / "empty_source_documents_effect_catalog_sources.json"
        with source_manifest.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "catalog_type": "effect_catalog_sources",
                    "catalog_version": 1,
                    "registrations": [
                        {
                            "effect_id": "empty-source-docs",
                            "mode": "builtin-seamless",
                            "effect_source": "builtin",
                            "family": "seamless",
                            "fx_id": "CES_PlugIn_Seamless.dll\\DSP_TR_SeamlessSliding_LC",
                            "style_hints": ["seamless"],
                            "retrieval_priority": 0,
                            "source_documents": [],
                        }
                    ],
                },
                handle,
                indent=2,
            )
            handle.write("\n")

        with self.assertRaisesRegex(ValueError, "must include non-empty string source_documents"):
            build_effect_catalog(HARNESS_ROOT.parent, source_manifest_path=source_manifest)

    def test_effect_catalog_audit_reports_manifest_alignment(self) -> None:
        audit = build_effect_catalog_audit(HARNESS_ROOT.parent)

        self.assertEqual(audit["report_type"], "effect_catalog_audit")
        self.assertEqual(audit["status"], "ok")
        self.assertEqual(audit["baseline_registration_count"], 24)
        self.assertEqual(audit["manifest_registration_count"], 24)
        self.assertEqual(audit["missing_effect_ids"], [])
        self.assertEqual(audit["extra_effect_ids"], [])
        self.assertEqual(len(audit["source_manifest_sha256"]), 64)

    def test_effect_catalog_audit_reports_missing_manifest(self) -> None:
        audit = build_effect_catalog_audit(self.root)

        self.assertEqual(audit["status"], "missing_source_manifest")
        self.assertEqual(audit["baseline_registration_count"], 24)
        self.assertEqual(audit["manifest_registration_count"], 0)
        self.assertEqual(
            audit["missing_effect_ids"],
            [
                "builtin-blur",
                "builtin-blur-diagblur",
                "builtin-blur-diamondbokeh",
                "builtin-blur-dimfade",
                "builtin-blur-fadeblur",
                "builtin-blur-hexbokeh",
                "builtin-blur-rotateblur",
                "builtin-blur-shakezoom",
                "builtin-blur-upgrow",
                "builtin-camcorder",
                "builtin-frame-overlay",
                "builtin-glitch",
                "builtin-glitch-distortion",
                "builtin-glitch-hdistortion",
                "builtin-glitch-hdistortion2",
                "builtin-glitch-stretch-swipe",
                "builtin-glitch-tunewave",
                "builtin-particle-spray",
                "builtin-seamless",
                "builtin-ui-app-swipe",
                "builtin-ui-rotate-face",
                "builtin-ui-snapshot",
                "generated-glitch-placeholder",
                "generated-seamless-placeholder",
            ],
        )
        self.assertEqual(audit["extra_effect_ids"], [])
        self.assertIsNone(audit["source_manifest_sha256"])

    def test_audit_effects_returns_nonzero_for_missing_manifest(self) -> None:
        Args = type(
            "Args",
            (),
            {
                "output": None,
                "source_manifest": str(self.root / "missing_effect_catalog_sources.json"),
            },
        )

        exit_code = _handle_audit_effects(Args(), HARNESS_ROOT.parent)
        self.assertEqual(exit_code, 1)

    def test_audit_effects_returns_zero_for_matching_manifest(self) -> None:
        Args = type("Args", (), {"output": None, "source_manifest": None})

        exit_code = _handle_audit_effects(Args(), HARNESS_ROOT.parent)
        self.assertEqual(exit_code, 0)

    def test_audit_effects_writes_audit_report(self) -> None:
        Args = type(
            "Args",
            (),
            {
                "output": str(self.root / "effect_catalog_audit.json"),
                "source_manifest": str(self.root / "missing_effect_catalog_sources.json"),
            },
        )

        exit_code = _handle_audit_effects(Args(), HARNESS_ROOT.parent)
        self.assertEqual(exit_code, 1)

        with (self.root / "effect_catalog_audit.json").open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["report_type"], "effect_catalog_audit")
        self.assertEqual(payload["status"], "missing_source_manifest")
        self.assertEqual(payload["baseline_registration_count"], 24)

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

    def _build_job(
        self,
        reference_transition: Path,
        frame_count: int,
        width: int | None = None,
        height: int | None = None,
    ) -> RenderJob:
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
                width=width if width is not None else self.width,
                height=height if height is not None else self.height,
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
