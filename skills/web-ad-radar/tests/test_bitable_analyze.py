from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from radar.bitable.analyze import AnalysisResult, filter_subset, write_back_to_bitable  # noqa: E402
from radar.bitable.dedup import url_hash  # noqa: E402
from radar.models import EmployerGuess, JobRecord  # noqa: E402


class FakeBitableClient:
    def __init__(self, url: str):
        self.url = url
        self.updated: list[tuple[str, dict]] = []

    def list_all_records(self):
        return [{"record_id": "rec-1", "fields": {"url_hash": url_hash(self.url)}}]

    def update_record(self, record_id: str, fields: dict) -> None:
        self.updated.append((record_id, fields))


class BitableAnalyzeWriteBackTest(unittest.TestCase):
    def test_write_back_includes_inference_process_fields(self):
        url = "https://example.com/job"
        result = AnalysisResult(
            job_id="source:1",
            url=url,
            industry="software",
            function="R&D",
            guessed_employer="ExampleAI",
            confidence="high",
            confidence_score=0.91,
            reasoning="Evidence chain summary",
            review_flags=["needs human review"],
            evidence=[{"type": "metaso_candidate_generation", "candidates": [{"name": "ExampleAI"}]}],
            external_sources=[{"title": "source", "url": "https://source"}],
            search_queries=["ExampleAI job JD"],
            cross_job_links=["source:2"],
            is_high_confidence=True,
        )
        client = FakeBitableClient(url)

        written = write_back_to_bitable(client, [result], run_id="run-1", model="MiniMax-M3")

        self.assertEqual(written, 1)
        _, fields = client.updated[0]
        self.assertEqual(fields["employer_guess"], "ExampleAI")
        self.assertEqual(fields["confidence"], 0.91)
        self.assertEqual(fields["analysis_status"], "success")
        self.assertEqual(fields["analysis_run_id"], "run-1")
        self.assertEqual(fields["analysis_model"], "MiniMax-M3")
        self.assertEqual(fields["reasoning_summary"], "Evidence chain summary")
        self.assertEqual(json.loads(fields["review_flags_json"]), ["needs human review"])
        self.assertEqual(json.loads(fields["search_queries_json"]), ["ExampleAI job JD"])
        self.assertEqual(json.loads(fields["external_sources_json"])[0]["title"], "source")
        self.assertEqual(json.loads(fields["evidence_json"])["inference_evidence"][0]["type"], "metaso_candidate_generation")


class BitableAnalyzeSubsetTest(unittest.TestCase):
    def test_high_confidence_flag_matches_point_eight_rubric(self):
        job = JobRecord("src", "Src", "AI Director", "https://e/ai")
        medium = EmployerGuess(job.id, "Example", "medium", 0.79)
        high = EmployerGuess(job.id, "Example", "high", 0.80)

        self.assertFalse(AnalysisResult.from_v1_guess(medium, job).is_high_confidence)
        self.assertTrue(AnalysisResult.from_v1_guess(high, job).is_high_confidence)

    def test_transient_inference_failures_remain_retryable(self):
        result = AnalysisResult(
            job_id="src:1",
            url="https://e/retry",
            industry="",
            function="",
            guessed_employer=None,
            confidence="low",
            confidence_score=0.0,
            reasoning="timeout",
            review_flags=["per_job_timeout: exceeded 1080s"],
        )

        self.assertTrue(result.is_retryable_failure())

    def test_daily_subset_requires_senior_tech_business_role_or_ai_leadership(self):
        jobs = [
            JobRecord("src", "Src", "Robotics Sales Director", "https://e/robotics-sales", industry_label="\u673a\u5668\u4eba", function_label="\u9500\u552e"),
            JobRecord("src", "Src", "Semiconductor R&D Manager", "https://e/semi-rd", industry_label="\u534a\u5bfc\u4f53", function_label="\u7814\u53d1"),
            JobRecord("src", "Src", "Consumer Electronics Operations VP", "https://e/consumer-electronics-ops", jd_text="Lead smart hardware product operations."),
            JobRecord("src", "Src", "AI Transformation Director", "https://e/ai-transformation", industry_label="\u6d88\u8d39", function_label="IT", jd_text="Lead enterprise AI adoption for a cosmetics group."),
            JobRecord("src", "Src", "Senior Algorithm Engineer", "https://e/ai-engineer", industry_label="\u673a\u5668\u4eba", function_label="\u7814\u53d1"),
            JobRecord("src", "Src", "HR Director", "https://e/hr-director", industry_label="\u534a\u5bfc\u4f53", function_label="\u4eba\u4e8b"),
            JobRecord("src", "Src", "Finance VP", "https://e/finance-vp", jd_text="AI software company finance leader."),
            JobRecord("src", "Src", "Sales Director", "https://e/chemical-sales", industry_label="\u5316\u5de5", function_label="\u9500\u552e"),
            JobRecord("src", "Src", "Assistant Manager Robotics Sales", "https://e/assistant-manager", industry_label="\u673a\u5668\u4eba", function_label="\u9500\u552e"),
            JobRecord("src", "Src", "Asia General Manager Sourcing & Quality", "https://e/sourcing-quality", industry_label="\u6d88\u8d39", function_label="\u4f9b\u5e94\u94fe", jd_text="Lead sourcing operations and enterprise AI adoption."),
        ]

        result = filter_subset(jobs)

        self.assertEqual(
            [job.url for job in result],
            [
                "https://e/robotics-sales",
                "https://e/consumer-electronics-ops",
                "https://e/ai-transformation",
            ],
        )


if __name__ == "__main__":
    unittest.main()
