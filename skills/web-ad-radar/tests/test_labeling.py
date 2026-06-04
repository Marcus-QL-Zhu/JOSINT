import unittest
import json

from radar.labeling import FUNCTION_LABELS, INDUSTRY_LABELS, label_job, label_jobs
from radar.models import JobRecord


class LabelingTest(unittest.TestCase):
    def test_robotics_rnd_job_gets_robotics_and_rnd_labels(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "产品经理（工业机器人）",
            "https://example.com/robot",
            detail_text="负责工业机器人控制系统、运动控制和产品研发。",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "研发")
        self.assertEqual(label.industry_label, "机器人")
        self.assertEqual(label.confidence, "high")
        self.assertTrue(label.evidence)

    def test_sap_job_gets_it_label_without_polluting_industry(self):
        job = JobRecord(
            "randstad",
            "Randstad China",
            "SAP SuccessFactors Manager",
            "https://example.com/sap",
            detail_text="负责SAP SuccessFactors、HRIS和企业系统实施。",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "IT")
        self.assertIsNone(label.industry_label)
        self.assertEqual(label.confidence, "medium")

    def test_semiconductor_sales_job_gets_sales_and_semiconductor(self):
        job = JobRecord(
            "rgf",
            "RGF Professional Recruitment China",
            "CD-Sem PSE",
            "https://example.com/sem",
            detail_text="半导体设备销售，负责客户开发和销售目标。",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "销售")
        self.assertEqual(label.industry_label, "半导体")

    def test_labels_are_limited_to_allowed_enums_or_none(self):
        job = JobRecord(
            "robert-half",
            "Robert Half China",
            "Mystery Role",
            "https://example.com/mystery",
            detail_text="No useful classification clues.",
        )

        label = label_job(job)

        self.assertIn(label.function_label, FUNCTION_LABELS | {None})
        self.assertIn(label.industry_label, INDUSTRY_LABELS | {None})

    def test_permanent_does_not_trigger_vc_pe(self):
        job = JobRecord(
            "robert-half",
            "Robert Half China",
            "IT Business Analyst",
            "https://example.com/it",
            detail_text="Permanent role responsible for ERP implementation.",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "IT")

    def test_persolkelly_brand_does_not_trigger_vc_pe(self):
        job = JobRecord(
            "persolkelly",
            "PERSOLKELLY China",
            "persolkelly日本人就職",
            "https://example.com/persol",
            jd_text="职位描述 PERSOLKELLY日本人就職",
        )

        label = label_job(job)

        self.assertIsNone(label.function_label)

    def test_recruitment_footer_does_not_force_professional_services_industry(self):
        job = JobRecord(
            "robert-half",
            "Robert Half China",
            "Artificial Intelligence Engineer",
            "https://example.com/ai",
            detail_text="Develop AI software products. Robert Half recruitment services footer.",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "研发")
        self.assertEqual(label.industry_label, "软件")

    def test_javascript_footer_does_not_override_ai_developer_title(self):
        job = JobRecord(
            "robert-half",
            "Robert Half China",
            "Artificial Intelligence (AI) Developer",
            "https://example.com/ai-dev",
            detail_text="function OptanonWrapper() { sales consulting recruitment cookies }",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "研发")
        self.assertEqual(label.industry_label, "软件")

    def test_javascript_footer_does_not_override_supply_chain_title(self):
        job = JobRecord(
            "robert-half",
            "Robert Half China",
            "Supply Chain Specialist",
            "https://example.com/supply-chain",
            detail_text="function OptanonWrapper() { sales consulting recruitment cookies }",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "供应链")
        self.assertIsNone(label.industry_label)

    def test_procurement_title_wins_over_rnd_mentions_in_jd(self):
        job = JobRecord(
            "persolkelly",
            "PERSOLKELLY China",
            "采购专员",
            "https://example.com/procurement",
            jd_text="作为集研发与制造业务于一体的企业，本岗位需统筹供应商管理、采购执行等全流程采购工作。",
        )

        label = label_job(job)

        self.assertEqual(label.function_label, "供应链")

    def test_layer4_batches_low_confidence_jobs_with_at_most_ten_per_minimax_call(self):
        class FakeMiniMax:
            def __init__(self):
                self.calls = []

            def chat(self, messages, **kwargs):
                payload = json.loads(messages[0]["content"])
                jobs = payload["jobs"]
                self.calls.append({"jobs": jobs, "kwargs": kwargs})
                return json.dumps(
                    {
                        "labels": [
                            {
                                "id": job["id"],
                                "function_label": "研发",
                                "industry_label": "机器人",
                                "confidence": "high",
                                "evidence": ["MiniMax-M2.7 batch label"],
                            }
                            for job in jobs
                        ]
                    },
                    ensure_ascii=False,
                )

        jobs = [
            JobRecord(
                "randstad",
                "Randstad China",
                f"Ambiguous Technical Role {index}",
                f"https://example.com/job-{index}",
                detail_text="The role works on embodied intelligence products without clear keyword signals.",
            )
            for index in range(11)
        ]
        minimax = FakeMiniMax()

        label_jobs(jobs, minimax=minimax)

        self.assertEqual([len(call["jobs"]) for call in minimax.calls], [10, 1])
        self.assertTrue(all(len(call["jobs"]) <= 10 for call in minimax.calls))
        self.assertTrue(all(job.function_label == "研发" for job in jobs))
        self.assertTrue(all(job.industry_label == "机器人" for job in jobs))
        self.assertTrue(all("MiniMax-M2.7 batch label" in job.label_evidence for job in jobs))

    def test_layer4_ignores_labels_outside_allowed_enums(self):
        class FakeMiniMax:
            def chat(self, messages, **kwargs):
                payload = json.loads(messages[0]["content"])
                return json.dumps(
                    {
                        "labels": [
                            {
                                "id": payload["jobs"][0]["id"],
                                "function_label": "not-a-function",
                                "industry_label": "not-an-industry",
                                "confidence": "high",
                                "evidence": ["bad output"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        job = JobRecord("randstad", "Randstad China", "Ambiguous Role", "https://example.com/ambiguous")

        label_jobs([job], minimax=FakeMiniMax())

        self.assertIsNone(job.function_label)
        self.assertIsNone(job.industry_label)
        self.assertEqual(job.label_confidence, "low")

    def test_layer4_prompt_sends_title_and_jd_as_separate_fields(self):
        class CapturingMiniMax:
            def __init__(self):
                self.payload = None

            def chat(self, messages, **kwargs):
                self.payload = json.loads(messages[0]["content"])
                job = self.payload["jobs"][0]
                return json.dumps(
                    {
                        "labels": [
                            {
                                "id": job["id"],
                                "function_label": None,
                                "industry_label": None,
                                "confidence": "low",
                                "evidence": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        job = JobRecord(
            "persolkelly",
            "PERSOLKELLY China",
            "Business Coordinator",
            "https://example.com/buyer",
            jd_text="负责跨部门项目协调、资料整理和业务流程跟进。公司介绍：某500强汽车零配件企业。",
            detail_text="legacy detail text should not be preferred when jd_text exists",
        )
        minimax = CapturingMiniMax()

        label_jobs([job], minimax=minimax)

        sent = minimax.payload["jobs"][0]
        self.assertEqual(sent["title"], "Business Coordinator")
        self.assertIn("跨部门项目协调", sent["jd_text"])
        self.assertNotIn("legacy detail text", sent["jd_text"])


if __name__ == "__main__":
    unittest.main()
