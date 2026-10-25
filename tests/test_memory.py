"""记忆能力测试: 意图路由 + Voicenotes 客户端(mock) + 降级行为 (无网络, 无真实令牌)."""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import router
import server
import voicenotes_client


class TestRouterClassify(unittest.TestCase):
    def test_create_with_content(self):
        intent, payload = router.classify("贾维斯，记一下，我的车停在B2层C36")
        self.assertEqual(intent, router.CREATE)
        self.assertIn("B2", payload)

    def test_create_too_short_needs_confirm(self):
        intent, _ = router.classify("记一下")
        self.assertEqual(intent, router.NEED_CONFIRM)

    def test_search_parking(self):
        intent, _ = router.classify("我的车停在哪里？")
        self.assertEqual(intent, router.SEARCH)

    def test_search_past_notes(self):
        intent, _ = router.classify("上周我关于AI Agent有哪些想法？")
        self.assertEqual(intent, router.SEARCH)

    def test_search_kinship_and_personal_data(self):
        for q in ("我妈生日是什么时候？", "我爸的电话是多少",
                  "我的航班是几点的？", "我老婆的生日是哪天"):
            intent, _ = router.classify(q)
            self.assertEqual(intent, router.SEARCH, q)

    def test_general_knowledge_not_search(self):
        # 知识性问句不得误入记忆检索
        for q in ("什么是通货膨胀", "美联储是干什么的？", "明天天气怎么样"):
            intent, _ = router.classify(q)
            self.assertEqual(intent, router.GENERAL, q)

    def test_summary_today(self):
        intent, payload = router.classify("总结一下今天做了什么")
        self.assertEqual(intent, router.SUMMARY)
        today = date.today().isoformat()
        self.assertEqual(payload, (today, today))

    def test_summary_last_week(self):
        intent, payload = router.classify("总结一下上周的记录")
        self.assertEqual(intent, router.SUMMARY)
        monday = date.today() - timedelta(days=date.today().weekday() + 7)
        self.assertEqual(payload[0], monday.isoformat())

    def test_general_question(self):
        intent, _ = router.classify("什么是美联储逆回购？")
        self.assertEqual(intent, router.GENERAL)

    def test_general_not_misrouted(self):
        intent, _ = router.classify("Python怎么读文件？")
        self.assertEqual(intent, router.GENERAL)


class FakeClient:
    """内存版 Voicenotes 客户端, 记录调用参数."""

    def __init__(self, search_text="B2层C36 (10月25日 09:12 的语音笔记)",
                 list_text="09:12 记录停车B2层C36；13:00 午饭花费86元", fail=False):
        self.search_text = search_text
        self.list_text = list_text
        self.fail = fail
        self.calls = []

    def search(self, query):
        self.calls.append(("search", query))
        if self.fail:
            raise voicenotes_client.VoicenotesUnavailable("模拟服务不可用")
        return self.search_text

    def list_notes(self, date_from=None, date_to=None):
        self.calls.append(("list", date_from, date_to))
        if self.fail:
            raise voicenotes_client.VoicenotesUnavailable("模拟服务不可用")
        return self.list_text

    def create_note(self, text, tags=None):
        self.calls.append(("create", text))
        if self.fail:
            raise voicenotes_client.VoicenotesUnavailable("模拟服务不可用")
        return "已创建笔记: " + text


class MemoryTestBase(unittest.TestCase):
    def setUp(self):
        self._saved = (server.VN_TOKEN, server.VN_ENABLED,
                       server._vn_client, server._vn_client_failed)
        server.VN_TOKEN = "test-token"
        server.VN_ENABLED = "auto"
        server._vn_client_failed = False
        server.PROVIDER = "demo"  # 无GLM密钥路径

    def tearDown(self):
        (server.VN_TOKEN, server.VN_ENABLED,
         server._vn_client, server._vn_client_failed) = self._saved


class TestMemoryHandlers(MemoryTestBase):
    def test_create_note_called_with_content(self):
        fake = FakeClient()
        server._vn_client = fake
        reply, provider, intent = server.ask("贾维斯，记一下，我的车停在B2层C36")
        self.assertEqual(intent, router.CREATE)
        self.assertIn("记好了", reply)
        kinds = [c[0] for c in fake.calls]
        self.assertIn("create", kinds)
        create_payload = [c[1] for c in fake.calls if c[0] == "create"][0]
        self.assertIn("B2层C36", create_payload)

    def test_search_returns_grounded_digest(self):
        fake = FakeClient()
        server._vn_client = fake
        reply, provider, intent = server.ask("我的车停在哪里？")
        self.assertEqual(intent, router.SEARCH)
        self.assertIn("B2", reply)  # 来自记录, 不是编造

    def test_search_empty_is_honest(self):
        server._vn_client = FakeClient(search_text="   ")
        reply, _, intent = server.ask("我的车停在哪里？")
        self.assertEqual(intent, router.SEARCH)
        self.assertIn("没有找到", reply)

    def test_summary_passes_date_range(self):
        fake = FakeClient()
        server._vn_client = fake
        reply, _, intent = server.ask("总结一下今天的记录")
        self.assertEqual(intent, router.SUMMARY)
        list_calls = [c for c in fake.calls if c[0] == "list"]
        self.assertTrue(list_calls)
        today = date.today().isoformat()
        self.assertEqual(list_calls[0][1], today)

    def test_mcp_failure_degrades_honestly(self):
        server._vn_client = FakeClient(fail=True)
        reply, provider, intent = server.ask("我的车停在哪里？")
        self.assertEqual(provider, "error")
        self.assertIn("连不上", reply)
        # 关键: 记忆失败不影响普通问答
        reply2, provider2, intent2 = server.ask("什么是美联储逆回购？")
        self.assertEqual(intent2, router.GENERAL)
        self.assertIn("演示模式", reply2)

    def test_memory_disabled_degrades(self):
        server.VN_TOKEN = ""
        server._vn_client = None
        reply, _, intent = server.ask("我的车停在哪里？")
        self.assertEqual(intent, router.SEARCH)
        self.assertIn("Voicenotes", reply)


class TestVoicenotesClient(unittest.TestCase):
    def test_no_token_raises(self):
        with self.assertRaises(voicenotes_client.VoicenotesUnavailable):
            voicenotes_client.VoicenotesClient("https://x/mcp", "")

    def test_sse_response_parsed(self):
        parsed = voicenotes_client.VoicenotesClient._parse_response(
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
        self.assertTrue(parsed["result"]["ok"])

    def test_json_response_parsed(self):
        parsed = voicenotes_client.VoicenotesClient._parse_response(
            '{"jsonrpc":"2.0","id":1,"result":{"ok":1}}')
        self.assertEqual(parsed["result"]["ok"], 1)

    def test_http_401_maps_to_reauth_message(self):
        import urllib.error

        def raise_401(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        client = voicenotes_client.VoicenotesClient("https://x/mcp", "tok")
        real = voicenotes_client.urllib.request.urlopen
        voicenotes_client.urllib.request.urlopen = raise_401
        try:
            with self.assertRaises(voicenotes_client.VoicenotesUnavailable) as ctx:
                client._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertIn("授权", str(ctx.exception))
        finally:
            voicenotes_client.urllib.request.urlopen = real


if __name__ == "__main__":
    unittest.main()
