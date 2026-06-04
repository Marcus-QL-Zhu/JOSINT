import tempfile
import unittest
from pathlib import Path

from radar.config import build_run_config, resolve_companies
from radar.env import load_env
from radar.models import JobRecord


class ConfigModelsTest(unittest.TestCase):
    def test_build_run_config_resolves_workspace_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            cfg = build_run_config(
                workspace=workspace,
                env_path=Path(".env"),
                output_dir=Path("reports"),
                data_dir=Path("data"),
                companies=None,
                crawl_only=True,
                date_from="2026-06-01",
                date_to="2026-06-04",
            )

            self.assertEqual(cfg.workspace, workspace.resolve())
            self.assertEqual(cfg.env_path, workspace.resolve() / ".env")
            self.assertEqual(cfg.output_dir, workspace.resolve() / "reports")
            self.assertEqual(cfg.data_dir, workspace.resolve() / "data")
            self.assertTrue(cfg.crawl_only)
            self.assertEqual(cfg.date_from, "2026-06-01")
            self.assertEqual(cfg.date_to, "2026-06-04")

    def test_resolve_companies_accepts_aliases_and_rejects_unknown(self):
        self.assertEqual(resolve_companies("rw,randstad"), ["robert-walters", "randstad"])
        with self.assertRaisesRegex(ValueError, "Unknown competitor"):
            resolve_companies("not-a-source")

    def test_load_env_reads_values_without_comments_or_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "# comment\n"
                "MINIMAX_API_KEY='secret-value'\n"
                "METASO_API_KEY=mk-test\n"
                "EMPTY=\n",
                encoding="utf-8",
            )

            values = load_env(env_file)

            self.assertEqual(values["MINIMAX_API_KEY"], "secret-value")
            self.assertEqual(values["METASO_API_KEY"], "mk-test")
            self.assertEqual(values["EMPTY"], "")

    def test_job_record_has_stable_id_from_source_and_url(self):
        job = JobRecord(
            source_slug="randstad",
            source_name="Randstad China",
            title="Finance Director",
            url="https://www.randstad.cn/jobs/finance-director_123/",
        )

        self.assertEqual(job.id, "randstad:c83a53845ace")
        self.assertEqual(job.canonical_url, job.url)


if __name__ == "__main__":
    unittest.main()
