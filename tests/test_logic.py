"""单元测试: Provider 逻辑与回答风格 (无需网络, 无需密钥)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_provider
import glm_provider


class TestDemoProvider(unittest.TestCase):
    def test_reply_is_chinese_and_speakable(self):
        reply = demo_provider.answer("什么是美联储逆回购？")
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 10)

    def test_reply_mentions_missing_key(self):
        reply = demo_provider.answer("任意问题")
        self.assertIn("GLM_API_KEY", reply)

    def test_reply_is_short_enough_for_voice(self):
        reply = demo_provider.answer("问题")
        self.assertLessEqual(len(reply), 120)

    def test_reply_has_no_markdown(self):
        reply = demo_provider.answer("问题")
        for marker in ("#", "*", "`", "- "):
            self.assertNotIn(marker, reply)


class TestGlmProviderPayload(unittest.TestCase):
    """用假 urlopen 验证请求构造与响应解析, 不发真实网络请求."""

    def setUp(self):
        self.captured = {}

        class FakeResponse:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(req, timeout=None):
            self.captured["url"] = req.full_url
            self.captured["auth"] = req.headers.get("Authorization")
            self.captured["body"] = req.data.decode("utf-8")
            self.captured["timeout"] = timeout
            return FakeResponse(json_ok_body)

        self.real_open = glm_provider.DIRECT_OPENER.open
        glm_provider.DIRECT_OPENER.open = fake_open

        json_ok_body = (
            '{"choices":[{"message":{"content":"逆回购是美联储卖出国债回收流动性。"}}]}'
        ).encode("utf-8")

    tearDown_restore = None

    def tearDown(self):
        glm_provider.DIRECT_OPENER.open = self.real_open

    def test_request_shape(self):
        glm_provider.answer("测试", "k-test", "glm-5.3-flash", "https://example/api/paas/v4/", 12)
        self.assertTrue(self.captured["url"].endswith("/chat/completions"))
        self.assertFalse(self.captured["url"].endswith("//chat/completions"))
        self.assertEqual(self.captured["auth"], "Bearer k-test")
        self.assertEqual(self.captured["timeout"], 12)

    def test_answer_extracted_from_content(self):
        reply = glm_provider.answer("q", "k", "m", "https://example/v4", 5)
        self.assertEqual(reply, "逆回购是美联储卖出国债回收流动性。")

    def test_reasoning_only_response_is_rejected(self):
        """v0.2: content为空时必须报错, 不得把推理过程当答案朗读."""
        class FakeResponse2:
            def read(self):
                return ('{"choices":[{"message":{"content":"",'
                        '"reasoning_content":"推理过程不应被朗读"}}]}').encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        glm_provider.DIRECT_OPENER.open = lambda req, timeout=None: FakeResponse2()
        with self.assertRaises(glm_provider.ProviderError):
            glm_provider.answer("q", "k", "m", "https://example/v4", 5)


class TestGlmProviderErrors(unittest.TestCase):
    def test_missing_key_raises_provider_error(self):
        with self.assertRaises(glm_provider.ProviderError):
            glm_provider.answer("q", "", "m", "https://example/v4", 5)

    def test_network_error_message_has_no_internal_url(self):
        """v0.2: 错误信息不得回显内部服务地址等配置."""
        import urllib.error

        def raise_url_error(req, timeout=None):
            raise urllib.error.URLError("[Errno 60] connect to secret-glm.internal:443 failed")

        real = glm_provider.DIRECT_OPENER.open
        glm_provider.DIRECT_OPENER.open = raise_url_error
        try:
            with self.assertRaises(glm_provider.ProviderError) as ctx:
                glm_provider.answer("q", "k", "m", "https://example/v4", 5)
            msg = str(ctx.exception)
            self.assertNotIn("secret-glm.internal", msg)
            self.assertNotIn("http", msg)
        finally:
            glm_provider.DIRECT_OPENER.open = real

    def test_http_401_mapped_to_friendly_message(self):
        import urllib.error

        def raise_401(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        real = glm_provider.DIRECT_OPENER.open
        glm_provider.DIRECT_OPENER.open = raise_401
        try:
            with self.assertRaises(glm_provider.ProviderError) as ctx:
                glm_provider.answer("q", "bad", "m", "https://example/v4", 5)
            self.assertIn("401", str(ctx.exception))
        finally:
            glm_provider.DIRECT_OPENER.open = real


if __name__ == "__main__":
    unittest.main()
