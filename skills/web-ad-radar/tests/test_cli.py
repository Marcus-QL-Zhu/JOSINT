import tempfile
import unittest
from pathlib import Path

from radar.cli import _build_label_client, _build_reasoning_client, main


class CliTest(unittest.TestCase):
    def test_cli_crawl_only_writes_report_and_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".env").write_text("MINIMAX_API_KEY=x\nMETASO_API_KEY=y\n", encoding="utf-8")

            exit_code = main(
                [
                    "--workspace",
                    str(workspace),
                    "--companies",
                    "randstad",
                    "--crawl-only",
                    "--from",
                    "2026-06-04",
                    "--to",
                    "2026-06-04",
                    "--offline-sample",
                ]
            )

            report = workspace / "reports" / "web-ad-radar-2026-06-04.md"
            self.assertEqual(exit_code, 0)
            self.assertTrue(report.exists())
            text = report.read_text(encoding="utf-8")
            self.assertIn("Sample Finance Director", text)
            self.assertIn("| Randstad | Sample Finance Director | German chemical company in Shanghai seeking finance leadership. | 财务 | 化工 | high | Shanghai | 2026-06-04 | https://example.com/randstad/sample-finance-director |  |  |", text)
            self.assertTrue((workspace / "data" / "jobs.sqlite").exists())

    def test_cli_rejects_unknown_company(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Unknown competitor"):
                main(["--workspace", tmp, "--companies", "bogus", "--crawl-only"])

    def test_cli_analysis_limit_limits_jobs_sent_to_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".env").write_text("MINIMAX_API_KEY=x\nMETASO_API_KEY=y\n", encoding="utf-8")

            exit_code = main(
                [
                    "--workspace",
                    str(workspace),
                    "--companies",
                    "randstad,rgf",
                    "--from",
                    "2026-06-04",
                    "--to",
                    "2026-06-04",
                    "--offline-sample",
                    "--analysis-limit",
                    "1",
                ]
            )

            report = (workspace / "reports" / "web-ad-radar-2026-06-04.md").read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn("- 爬取职位数: 2", report)
            self.assertIn("- 已分析职位数: 1", report)

    def test_label_client_defaults_to_minimax_m27_highspeed(self):
        client = _build_label_client({"MINIMAX_API_KEY": "x"})

        self.assertEqual(client.model, "MiniMax-M2.7-highspeed")
        self.assertEqual(client.base_url, "https://api.minimaxi.com/v1/chat/completions")

    def test_reasoning_client_uses_longer_default_timeout_for_m3(self):
        client = _build_reasoning_client({"MINIMAX_API_KEY": "x"})

        self.assertEqual(client.model, "MiniMax-M3")
        self.assertGreaterEqual(client.timeout, 180)


if __name__ == "__main__":
    unittest.main()
