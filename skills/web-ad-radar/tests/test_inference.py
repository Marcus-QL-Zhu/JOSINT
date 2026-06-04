import json
import unittest

from radar.inference import analyze_jobs, cluster_related_jobs, detect_proprietary_terms
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
    def __init__(self):
        self.queries = []

    def search(self, q):
        self.queries.append(q)
        return {"webpages": [{"title": "Honeywell Operating System", "link": "https://example.com/hos", "summary": "HOS is used by Honeywell."}]}


class InferenceTest(unittest.TestCase):
    def test_detect_proprietary_terms_finds_known_company_clues(self):
        job = JobRecord(
            source_slug="randstad",
            source_name="Randstad China",
            title="Operations Manager",
            url="https://example.com/job",
            detail_text="Lead HOS deployment and continuous improvement.",
        )

        terms = detect_proprietary_terms(job)

        self.assertEqual(terms[0]["text"], "HOS")
        self.assertIn("Honeywell", terms[0]["interpretation"])

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
            detail_text="Lead HOS deployment.",
        )
        minimax = FakeMiniMax(json.dumps({"guessed_employer": "Honeywell", "confidence": "high", "confidence_score": 0.91, "reasoning_summary": "HOS clue."}))

        metaso = FakeMetaso()
        guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertEqual(guesses[job.id].guessed_employer, "Honeywell")
        self.assertEqual(guesses[job.id].confidence, "high")
        self.assertEqual(guesses[job.id].evidence[0]["text"], "HOS")
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
        minimax = FakeMiniMax(json.dumps({"guessed_employer": None, "confidence": "low", "confidence_score": 0.1, "reasoning_summary": "Generic clue."}))
        metaso = FakeMetaso()

        analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertIn("German chemical company Shanghai employer", metaso.queries)

    def test_analyze_jobs_verifies_candidate_employer_has_similar_public_jd(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "Operations Manager",
            "https://e/1",
            location="Shanghai",
            detail_text="Lead HOS deployment and lean manufacturing transformation.",
        )
        minimax = SequenceMiniMax(
            [
                json.dumps({"guessed_employer": "Honeywell", "confidence": "medium", "confidence_score": 0.7, "reasoning_summary": "HOS clue."}),
                json.dumps({"guessed_employer": "Honeywell", "confidence": "high", "confidence_score": 0.88, "reasoning_summary": "Candidate employer has similar public JD."}),
            ]
        )
        metaso = FakeMetaso()

        guesses = analyze_jobs([job], minimax=minimax, metaso=metaso)

        self.assertIn("Honeywell Operations Manager Shanghai job JD", metaso.queries)
        self.assertEqual(len(minimax.calls), 2)
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
        minimax = FakeMiniMax(json.dumps({"guessed_employer": None, "confidence": "low", "confidence_score": 0.1, "reasoning_summary": "证据不足。"}))

        analyze_jobs([job], minimax=minimax, metaso=None)

        prompt = minimax.calls[0]["messages"][0]["content"]
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


if __name__ == "__main__":
    unittest.main()
