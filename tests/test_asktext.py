"""v0.3 集成测试: POST /ask-text 纯文本语音接口.

该接口的目标是消除 Apple Watch 对 JSON/字典解析的依赖:
响应体必须永远只是一段可朗读的中文, Content-Type 为 text/plain.
"""
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
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class TestAskText(unittest.TestCase):
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

    def ask_text(self, body=None, headers=None):
        req = urllib.request.Request(self.base + "/ask-text", method="POST")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body if body is not None else {"question": "你好"}).encode()
        try:
            with OPENER.open(req, data=data, timeout=10) as resp:
                return (resp.status, resp.headers.get("Content-Type", ""),
                        resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return (e.code, e.headers.get("Content-Type", ""),
                    e.read().decode("utf-8"))

    def test_ok_returns_plain_text_only(self):
        code, ctype, body = self.ask_text(
            {"question": "什么是通货膨胀", "session": "watch"},
            {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        # 响应体绝不能是 JSON: 手表直接朗读整个 body
        self.assertNotIn("{", body)
        self.assertNotIn('"', body)
        self.assertTrue(len(body.strip()) > 0)

    def test_401_is_plain_text(self):
        code, ctype, body = self.ask_text({"question": "你好"}, {"X-Jarvis-Key": "bad"})
        self.assertEqual(code, 401)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        self.assertNotIn("{", body)

    def test_empty_question_400_plain_text(self):
        code, ctype, body = self.ask_text(
            {"question": "", "session": "watch"}, {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 400)
        self.assertTrue(ctype.startswith("text/plain"), ctype)

    def test_invalid_json_400_plain_text(self):
        req = urllib.request.Request(self.base + "/ask-text", method="POST")
        req.add_header("X-Jarvis-Key", TEST_TOKEN)
        req.add_header("Content-Type", "application/json")
        try:
            with OPENER.open(req, data=b"not-json", timeout=10) as resp:
                code, ctype, body = resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()
        except urllib.error.HTTPError as e:
            code, ctype, body = e.code, e.headers.get("Content-Type", ""), e.read().decode()
        self.assertEqual(code, 400)
        self.assertTrue(ctype.startswith("text/plain"), ctype)

    def test_body_too_large_413(self):
        code, ctype, body = self.ask_text(
            {"question": "长" * 70000, "session": "watch"}, {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 413)
        self.assertTrue(ctype.startswith("text/plain"), ctype)

    def test_json_endpoint_still_json_on_error(self):
        """旧 /ask 的 JSON 契约不能被纯文本改造破坏."""
        req = urllib.request.Request(self.base + "/ask", method="POST")
        req.add_header("X-Jarvis-Key", TEST_TOKEN)
        req.add_header("Content-Type", "application/json")
        try:
            with OPENER.open(req, data=json.dumps({"question": ""}).encode(), timeout=10) as resp:
                code, ctype, raw = resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()
        except urllib.error.HTTPError as e:
            code, ctype, raw = e.code, e.headers.get("Content-Type", ""), e.read().decode()
        self.assertEqual(code, 400)
        self.assertTrue(ctype.startswith("application/json"), ctype)
        self.assertEqual(json.loads(raw)["error"], "empty_question")

    def test_request_id_header_present(self):
        code, ctype, body = self.ask_text(
            {"question": "你好"}, {"X-Jarvis-Key": TEST_TOKEN})
        self.assertEqual(code, 200)

    def test_ask_text_logs_never_contain_question(self):
        import io
        import contextlib
        secret = "纯文本接口隐私检查QAZ123"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code, _, _ = self.ask_text({"question": secret, "session": "watch"},
                                       {"X-Jarvis-Key": TEST_TOKEN})
            self.assertEqual(code, 200)
        self.assertNotIn(secret, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
