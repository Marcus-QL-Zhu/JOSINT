import json
import tempfile
import unittest
from pathlib import Path

from radar.llm_minimax import MiniMaxClient
from radar.metaso import MetasoClient, MetasoError
from radar.usage import ApiUsageLogger


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)

    def json(self):
        return self._body


class ApiClientTest(unittest.TestCase):
    def test_minimax_chat_uses_openai_endpoint_and_thinking_controls(self):
        calls = []

        def fake_post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return FakeResponse(body={"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        client = MiniMaxClient(
            api_key="secret-key",
            base_url="https://api.minimaxi.com/v1/chat/completions",
            model="MiniMax-M3",
            post=fake_post,
        )

        content = client.chat([{"role": "user", "content": "ping"}], thinking_type="adaptive", reasoning_split=True)

        self.assertEqual(content, "{\"ok\": true}")
        self.assertEqual(calls[0]["url"], "https://api.minimaxi.com/v1/chat/completions")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(calls[0]["json"]["model"], "MiniMax-M3")
        self.assertEqual(calls[0]["json"]["thinking"], {"type": "adaptive"})
        self.assertTrue(calls[0]["json"]["reasoning_split"])

    def test_minimax_chat_writes_api_usage_log_without_secret(self):
        def fake_post(url, headers, json, timeout):
            return FakeResponse(body={"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "api_usage.jsonl"
            client = MiniMaxClient(
                api_key="secret-key",
                base_url="https://api.minimaxi.com/v1/chat/completions",
                model="MiniMax-M2.7-highspeed",
                post=fake_post,
                usage_logger=ApiUsageLogger(usage_path),
                stage="label",
            )

            client.chat(
                [{"role": "user", "content": "ping"}],
                usage_context={"job_ids": ["job-1", "job-2"], "batch_size": 2},
            )

            record = json.loads(usage_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["provider"], "minimax")
            self.assertEqual(record["stage"], "label")
            self.assertEqual(record["model"], "MiniMax-M2.7-highspeed")
            self.assertEqual(record["batch_size"], 2)
            self.assertEqual(record["job_ids"], ["job-1", "job-2"])
            self.assertTrue(record["success"])
            self.assertNotIn("secret-key", usage_path.read_text(encoding="utf-8"))

    def test_metaso_search_rejects_body_level_errors(self):
        def fake_post(url, headers, json, timeout):
            return FakeResponse(body={"errCode": 1000, "errMsg": "bad params"})

        client = MetasoClient(api_key="mk-secret", post=fake_post)

        with self.assertRaisesRegex(MetasoError, "bad params"):
            client.search("OpenAI")

    def test_metaso_search_reader_and_chat_shapes(self):
        calls = []

        def fake_post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json})
            if url.endswith("/search"):
                return FakeResponse(body={"webpages": [{"title": "T", "link": "https://x"}], "total": 1})
            if url.endswith("/reader"):
                return FakeResponse(body={"title": "Page", "markdown": "# Page"})
            return FakeResponse(body={"answer": "A", "sources": []})

        client = MetasoClient(api_key="mk-secret", post=fake_post)

        self.assertEqual(client.search("OpenAI")["total"], 1)
        self.assertEqual(client.read("https://example.com")["markdown"], "# Page")
        self.assertEqual(client.ask_simple("question")["answer"], "A")
        self.assertEqual(calls[0]["json"], {"q": "OpenAI", "scope": "webpage", "includeSummary": True, "conciseSnippet": True})
        self.assertEqual(calls[1]["json"], {"url": "https://example.com"})
        self.assertEqual(calls[2]["json"], {"q": "question", "model": "fast", "format": "simple"})


if __name__ == "__main__":
    unittest.main()
