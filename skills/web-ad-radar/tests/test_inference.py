import json
import os
import tempfile
import unittest
from pathlib import Path

from radar.inference import analyze_jobs, cluster_related_jobs
from radar.models import JobRecord


class FakeMiniMax:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return self.content


class SequenceMiniMax:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return self.contents.pop(0)


class FakeMetaso:
    def __init__(self, candidates=None):
        self.queries = []
        self.chat_calls = []
        self.candidates = [{"name": "ExampleCorp", "supporting_clues": ["specific match"]}] if candidates is None else candidates

    def chat(self, messages, stream=False):
        self.chat_calls.append({"messages": messages, "stream": stream})
        return {
            "choices": [{"message": {"content": json.dumps({"candidates": self.candidates}, ensure_ascii=False)}}],
        }

    def search(self, q):
        self.queries.append(q)
        return {"webpages": [{"title": "Public employer job", "link": "https://example.com/job", "summary": "A similar public role."}]}


class InferenceTest(unittest.TestCase):
    def test_cluster_related_jobs_uses_shared_description_tokens(self):
        jobs = [
            JobRecord("randstad", "Randstad China", "Finance Director", "https://e/1", location="Shanghai", detail_text="German chemical company in Shanghai"),
            JobRecord("randstad", "Randstad China", "Legal Counsel", "https://e/2", location="Shanghai", detail_text="German chemical company legal team"),
            JobRecord("randstad", "Randstad China", "AI Engineer", "https://e/3", location="Beijing", detail_text="Embodied AI startup"),
        ]

        clusters = cluster_related_jobs(jobs)

        self.assertEqual(len(clusters[jobs[0].id]), 1)
        self.assertEqual(clusters[jobs[0].id][0].id, jobs[1].id)
        self.assertEqual(clusters[jobs[2].id], [])

    def test_analyze_jobs_uses_minimax_json_and_metaso_evidence(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "Operations Manager",
            "https://e/1",
            detail_text="Lead an internal operating-system transformation.",
        )
        minimax = FakeMiniMax(json.dumps({"guessed_employer": "ExampleCorp", "confidence": "high", "confidence_score": 0.91, "confidence_basis": "near_duplicate_public_jd", "reasoning_summary": "公开证据吻合。"}, ensure_ascii=False))

        metaso = FakeMetaso()
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("JOSINT_INFERENCE_DEBUG_DIR")
            os.environ["JOSINT_INFERENCE_DEBUG_DIR"] = tmp
            try:
                guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)
            finally:
                if old is None:
                    os.environ.pop("JOSINT_INFERENCE_DEBUG_DIR", None)
                else:
                    os.environ["JOSINT_INFERENCE_DEBUG_DIR"] = old

        self.assertEqual(guesses[job.id].guessed_employer, "ExampleCorp")
        self.assertEqual(guesses[job.id].confidence, "high")
        self.assertFalse(any(item["type"] == "proprietary_term" for item in guesses[job.id].evidence))
        self.assertTrue(metaso.queries)
        self.assertTrue(minimax.calls[0]["kwargs"]["reasoning_split"])

    def test_analyze_jobs_searches_company_description_clues(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "Finance Director",
            "https://e/1",
            location="Shanghai",
            detail_text="German chemical company in Shanghai seeking finance leadership.",
        )
        minimax = FakeMiniMax(json.dumps({"guessed_employer": None, "confidence": "low", "confidence_score": 0.1, "confidence_basis": "insufficient_evidence", "reasoning_summary": "线索过于宽泛。"}, ensure_ascii=False))
        metaso = FakeMetaso()

        analyze_jobs([job], minimax=minimax, metaso=metaso)

        candidate_prompt = metaso.chat_calls[0]["messages"][1]["content"]
        self.assertIn("German chemical company in Shanghai", candidate_prompt)

    def test_analyze_jobs_verifies_candidate_employer_has_similar_public_jd(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "Operations Manager",
            "https://e/1",
            location="Shanghai",
            detail_text="Lead a proprietary platform and lean manufacturing transformation.",
        )
        minimax = FakeMiniMax(json.dumps({
            "guessed_employer": "ExampleCorp",
            "confidence": "high",
            "confidence_score": 0.88,
            "confidence_basis": "near_duplicate_public_jd",
            "reasoning_summary": "候选雇主存在相似公开职位。",
        }, ensure_ascii=False))
        metaso = FakeMetaso()

        guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertTrue(any(query.startswith("ExampleCorp Operations Manager Shanghai") for query in metaso.queries))
        self.assertEqual(len(minimax.calls), 1)
        self.assertEqual(guesses[job.id].confidence, "high")
        self.assertTrue(any(item["type"] == "candidate_jd_check" for item in guesses[job.id].evidence))

    def test_reasoner_prompt_requires_chinese_output(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "Sales Director",
            "https://e/1",
            detail_text="Anonymous industrial company.",
        )
        minimax = FakeMiniMax(json.dumps({"guessed_employer": None, "confidence": "low", "confidence_score": 0.1, "confidence_basis": "insufficient_evidence", "reasoning_summary": "证据不足。"}))

        analyze_jobs([job], minimax=minimax, metaso=None)

        messages = minimax.calls[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("深圳、3C、DTC", messages[0]["content"])
        prompt = messages[1]["content"]
        self.assertIn("所有自然语言字段必须使用简体中文", prompt)
        self.assertIn("reasoning_summary", prompt)
        self.assertIn("review_flags", prompt)

    def test_analyze_jobs_returns_low_confidence_when_apis_fail(self):
        class BrokenMiniMax:
            def chat(self, messages, **kwargs):
                raise RuntimeError("api down")

        job = JobRecord("randstad", "Randstad China", "Unknown Role", "https://e/1", detail_text="Stealth client")

        guesses = analyze_jobs([job], minimax=BrokenMiniMax(), metaso=None)

        self.assertIsNone(guesses[job.id].guessed_employer)
        self.assertEqual(guesses[job.id].confidence, "low")
        self.assertIn("Inference API failed", guesses[job.id].review_flags[0])



    def test_invalid_initial_json_is_repaired_with_m3(self):
        job = JobRecord(
            "morgan-philips",
            "Morgan Philips Mainland China",
            "Commercial Lead",
            "https://e/1",
            detail_text="AI solutions commercial leadership role.",
        )
        minimax = SequenceMiniMax(
            [
                '{"guessed_employer": "StepFun" "confidence": "high"}',
                json.dumps({
                    "guessed_employer": "StepFun",
                    "confidence": "high",
                    "confidence_score": 0.82,
                    "confidence_basis": "multiple_specific_clues",
                    "reasoning_summary": "修复后的 JSON。",
                    "review_flags": [],
                }, ensure_ascii=False),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("JOSINT_INFERENCE_DEBUG_DIR")
            os.environ["JOSINT_INFERENCE_DEBUG_DIR"] = tmp
            try:
                guesses = analyze_jobs([job], minimax=minimax, metaso=None)
                debug_files = list(Path(tmp).glob("*.json"))
            finally:
                if old is None:
                    os.environ.pop("JOSINT_INFERENCE_DEBUG_DIR", None)
                else:
                    os.environ["JOSINT_INFERENCE_DEBUG_DIR"] = old

        self.assertEqual(guesses[job.id].guessed_employer, "StepFun")
        self.assertEqual(len(minimax.calls), 2)
        repair_prompt = minimax.calls[1]["messages"][0]["content"]
        self.assertIn("raw_response", repair_prompt)
        self.assertIn("不要重新推理", repair_prompt)
        self.assertTrue(debug_files)

    def test_metaso_accepts_fewer_than_three_candidates_without_padding(self):
        job = JobRecord(
            "morgan-philips",
            "Morgan Philips Mainland China",
            "Commercial Lead",
            "https://e/1",
            location="Shanghai",
            detail_text="AI solutions commercial leadership role.",
        )
        minimax = FakeMiniMax(json.dumps({
            "guessed_employer": "StepFun",
            "confidence": "medium",
            "confidence_score": 0.7,
            "confidence_basis": "multiple_specific_clues",
            "reasoning_summary": "候选得到多条具体线索支持。",
            "review_flags": [],
        }, ensure_ascii=False))
        metaso = FakeMetaso(candidates=[{"name": "StepFun", "supporting_clues": ["AI 产品线索"]}])

        guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertEqual(guesses[job.id].guessed_employer, "StepFun")
        self.assertEqual(guesses[job.id].confidence, "medium")
        self.assertEqual(len(metaso.queries), 1)
        self.assertTrue(any(item["type"] == "candidate_jd_check" for item in guesses[job.id].evidence))

    def test_metaso_empty_candidates_still_runs_reasoner_without_search(self):
        job = JobRecord("morgan-philips", "Morgan Philips", "AI Director", "https://e/2", detail_text="Confidential AI leadership role.")
        minimax = FakeMiniMax(json.dumps({
            "guessed_employer": None,
            "confidence": "low",
            "confidence_score": 0.0,
            "confidence_basis": "insufficient_evidence",
            "reasoning_summary": "没有可靠候选。",
        }, ensure_ascii=False))
        metaso = FakeMetaso(candidates=[])

        guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertIsNone(guesses[job.id].guessed_employer)
        self.assertEqual(metaso.queries, [])
        prompt = json.loads(minimax.calls[0]["messages"][1]["content"])
        self.assertEqual(prompt["metaso_candidates"], [])

    def test_generic_profile_only_is_capped_at_low_confidence(self):
        job = JobRecord("randstad", "Randstad China", "Ecommerce Director", "https://e/3", detail_text="深圳 3C DTC Shopify company.")
        minimax = FakeMiniMax(json.dumps({
            "guessed_employer": "Anker Innovations",
            "confidence": "high",
            "confidence_score": 0.95,
            "confidence_basis": "generic_profile_only",
            "reasoning_summary": "仅有通用画像。",
        }, ensure_ascii=False))

        guesses = analyze_jobs([job], minimax=minimax, metaso=None)

        self.assertEqual(guesses[job.id].confidence, "low")
        self.assertEqual(guesses[job.id].confidence_score, 0.49)

    def test_legacy_response_without_confidence_basis_preserves_score(self):
        job = JobRecord("randstad", "Randstad China", "R&D Director", "https://e/4", detail_text="Distinctive product and public JD match.")
        minimax = FakeMiniMax(json.dumps({
            "guessed_employer": "LegacyCorp",
            "confidence": "high",
            "confidence_score": 0.9,
            "reasoning_summary": "旧版模型响应。",
        }, ensure_ascii=False))

        guesses = analyze_jobs([job], minimax=minimax, metaso=None)

        self.assertEqual(guesses[job.id].confidence, "high")
        self.assertEqual(guesses[job.id].confidence_score, 0.9)

    def test_valid_json_with_wrong_top_level_shape_triggers_repair(self):
        job = JobRecord("randstad", "Randstad China", "Sales Director", "https://e/5", detail_text="Distinctive client description.")
        minimax = SequenceMiniMax([
            "[]",
            json.dumps({
                "guessed_employer": None,
                "confidence": "low",
                "confidence_score": 0.0,
                "confidence_basis": "insufficient_evidence",
                "reasoning_summary": "证据不足。",
                "review_flags": [],
            }, ensure_ascii=False),
        ])

        guesses = analyze_jobs([job], minimax=minimax, metaso=None)

        self.assertIsNone(guesses[job.id].guessed_employer)
        self.assertEqual(len(minimax.calls), 2)
if __name__ == "__main__":
    unittest.main()
