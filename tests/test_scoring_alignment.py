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
from overlay_harness.cli import _build_plan_comparison_report
from overlay_harness.cli import _build_run_evaluation_summary
from overlay_harness.cli import _handle_audit_effects
from overlay_harness.cli import _handle_index_effects
from overlay_harness.cli import _execute_job_command
from overlay_harness.cli import _summarize_retrieval_fields
from overlay_harness.cli import _summarize_retrieval_from_evaluation
from overlay_harness.cli import _summarize_smoke_test_retrieval
from overlay_harness.cli import _resolve_run_report_status
from overlay_harness.cli import _resolve_run_report_summary
from overlay_harness.effect_catalog import build_effect_catalog
from overlay_harness.effect_catalog import build_effect_catalog_audit
from overlay_harness.effect_catalog import load_effect_catalog
from overlay_harness.effect_catalog import select_effect_candidate
from overlay_harness.evaluator import score_frame_sequences
from overlay_harness.models import EffectSpec, InputSpec, RenderJob, RenderSettings
from overlay_harness.planner import build_recommended_plan
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
        self.assertEqual(summary["planning"]["retrieval_status"], "retrieved")
        self.assertEqual(summary["planning"]["retrieval_effect_id"], "builtin-glitch")
        self.assertEqual(summary["planning"]["retrieval_mode"], "builtin-glitch")
        self.assertEqual(summary["planning"]["retrieval_match_kind"], "alias")
        self.assertEqual(summary["planning"]["retrieval_matched_style_hint"], "glitch")
        self.assertEqual(summary["planning"]["retrieval_candidate_count"], 1)

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

    def test_run_command_result_includes_planning_retrieval_summary(self) -> None:
        job_path = self.root / "job.json"
        job_data = json.loads((HARNESS_ROOT.parent / "harness/examples/render_job.sample.json").read_text(encoding="utf-8"))
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
        ui = select_effect_candidate(catalog, style="ui", input_kind="real")
        ui_app_swipe = select_effect_candidate(catalog, style="ui-app-swipe", input_kind="real")
        ui_rotate_face = select_effect_candidate(catalog, style="ui-rotate-face", input_kind="real")
        glitch_hdistortion = select_effect_candidate(catalog, style="glitch-hdistortion", input_kind="real")
        glitch_hdistortion2 = select_effect_candidate(catalog, style="glitch-hdistortion2", input_kind="real")
        glitch_stretch_swipe = select_effect_candidate(catalog, style="glitch-stretch-swipe", input_kind="real")
        glitch_tunewave = select_effect_candidate(catalog, style="glitch-tunewave", input_kind="real")
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
        self.assertIsNotNone(ui)
        self.assertIsNotNone(ui_app_swipe)
        self.assertIsNotNone(ui_rotate_face)
        self.assertIsNotNone(glitch_hdistortion)
        self.assertIsNotNone(glitch_hdistortion2)
        self.assertIsNotNone(glitch_stretch_swipe)
        self.assertIsNotNone(glitch_tunewave)
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
        self.assertEqual(ui["effect_id"], "builtin-ui-snapshot")
        self.assertEqual(ui_app_swipe["effect_id"], "builtin-ui-app-swipe")
        self.assertEqual(ui_rotate_face["effect_id"], "builtin-ui-rotate-face")
        self.assertEqual(glitch_hdistortion["effect_id"], "builtin-glitch-hdistortion")
        self.assertEqual(glitch_hdistortion2["effect_id"], "builtin-glitch-hdistortion2")
        self.assertEqual(glitch_stretch_swipe["effect_id"], "builtin-glitch-stretch-swipe")
        self.assertEqual(glitch_tunewave["effect_id"], "builtin-glitch-tunewave")
        self.assertEqual(distortion["effect_id"], "builtin-glitch-distortion")
        self.assertEqual(camcorder["effect_id"], "builtin-camcorder")
        self.assertEqual(particle["effect_id"], "builtin-particle-spray")
        self.assertEqual(frame_overlay["effect_id"], "builtin-frame-overlay")
        self.assertEqual(blur_hexbokeh["match_kind"], "exact")
        self.assertEqual(glitch_tunewave["match_kind"], "exact")

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
