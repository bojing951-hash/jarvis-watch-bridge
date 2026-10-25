"""集成测试: 真实 HTTP 请求验证 /health 与 /ask 认证 (本机回环, 临时端口, Demo模式)."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server

TEST_TOKEN = "unit-test-token-1234567890"

# 无代理 opener: 系统Clash代理会拦截127.0.0.1回环请求, 测试必须直连
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.TOKEN = TEST_TOKEN
        server.PROVIDER = "demo"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with OPENER.open(req, data=data, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_health_returns_200(self):
        code, body = self.request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["provider"], "demo")

    def test_ask_without_key_returns_401(self):
        code, body = self.request("/ask", "POST", {"question": "你好", "session": "watch"})
        self.assertEqual(code, 401)
        self.assertIn("error", body)

    def test_ask_with_wrong_key_returns_401(self):
        code, body = self.request("/ask", "POST", {"question": "你好", "session": "watch"},
                                  {"X-Jarvis-Key": "wrong-key"})
        self.assertEqual(code, 401)

    def test_ask_with_valid_key_returns_reply(self):
        code, body = self.request("/ask", "POST", {"question": "什么是美联储逆回购？", "session": "watch"},
                                  {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 200)
        self.assertIn("reply", body)
        self.assertTrue(isinstance(body["reply"], str) and body["reply"])
        self.assertEqual(body["provider"], "demo")

    def test_ask_empty_question_returns_400(self):
        code, body = self.request("/ask", "POST", {"question": "", "session": "watch"},
                                  {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 400)

    def test_unknown_path_returns_404(self):
        code, _ = self.request("/nope")
        self.assertEqual(code, 404)

    # ---- v0.2 新增 ----

    def test_ask_too_long_question_returns_400(self):
        code, body = self.request("/ask", "POST",
                                  {"question": "长" * 600, "session": "watch"},
                                  {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "question_too_long")
        self.assertIn("reply", body)

    def test_ask_response_contains_intent(self):
        code, body = self.request("/ask", "POST",
                                  {"question": "什么是逆回购？", "session": "watch"},
                                  {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 200)
        self.assertEqual(body["intent"], "general_question")

    def test_metrics_requires_auth(self):
        code, _ = self.request("/metrics")
        self.assertEqual(code, 401)

    def test_metrics_shape(self):
        code, body = self.request("/metrics", headers={"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 200)
        for key in ("requests", "http_success_rate", "answer_success_rate",
                    "answers_ok", "answers_degraded", "answers_failed",
                    "avg_latency_ms", "intents", "model_calls",
                    "search_calls", "mcp_calls"):
            self.assertIn(key, body)

    def test_health_reports_memory_and_version(self):
        code, body = self.request("/health")
        self.assertEqual(code, 200)
        self.assertIn(body["memory"], ("enabled", "disabled"))
        self.assertEqual(body["version"], "0.3")

    def test_logs_never_contain_question_text(self):
        """v0.2 隐私要求: 服务端日志不得出现问题正文."""
        import io
        import contextlib
        secret = "绝密隐私问题XYZ789"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code, _ = self.request("/ask", "POST",
                                   {"question": secret, "session": "watch"},
                                   {"X-Jarvis-Key": TEST_TOKEN})
            self.assertEqual(code, 200)
        self.assertNotIn(secret, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
