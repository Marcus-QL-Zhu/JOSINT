from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from radar.bitable.analyze import AnalysisResult, write_back_to_bitable  # noqa: E402
from radar.bitable.dedup import url_hash  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
