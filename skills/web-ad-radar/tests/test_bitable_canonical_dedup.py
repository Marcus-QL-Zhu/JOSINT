from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from radar.bitable.dedup import Deduper


class _Client:
    def list_all_records(self):
        return [{
            "record_id": "rec-1",
            "fields": {
                "canonical_job_id": [{"text": "job:canonical", "type": "text"}],
                "url_hash": [{"text": "hash", "type": "text"}],
            },
        }]


class CanonicalDedupEncodingTest(unittest.TestCase):
    def test_feishu_text_wrapper_is_indexed(self):
        result = Deduper(_Client()).lookup(
            "https://watchjobs.net/job/new-source",
            canonical_job_id="job:canonical",
        )
        self.assertEqual("update_canonical", result.action)
        self.assertEqual("rec-1", result.record_id)


if __name__ == "__main__":
    unittest.main()
