"""v0.3 单元测试: 联网搜索模块 + 实时问答路由 + 时区边界 + 创建防重复."""
import os
import sys
import unittest
from datetime import date, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import router
import web_search


class TestNeedSearch(unittest.TestCase):
    def test_realtime_questions_search(self):
        for q in ("离岸人民币现在是在升值还是贬值？",
                  "今天天气怎么样", "美元兑人民币汇率多少",
                  "最新的AI新闻有哪些", "苹果股价涨了吗",
                  "帮我搜一下美联储最新政策", "现在几点了"):
            self.assertTrue(web_search.need_search(q), q)

    def test_concept_questions_no_search(self):
        for q in ("什么是离岸人民币", "什么是通货膨胀",
                  "怎么理解复利", "美联储是干什么的"):
            self.assertFalse(web_search.need_search(q), q)

    def test_empty_no_search(self):
        self.assertFalse(web_search.need_search(""))
        self.assertFalse(web_search.need_search(None))

    def test_private_questions_never_searched(self):
        """隐私护栏: 私人指涉问题不得发往公开搜索服务."""
        for q in ("今天我花了多少钱", "我的车值多少钱", "我买了什么",
                  "我妈的身体怎么样", "昨天我花了多少"):
            self.assertFalse(web_search.need_search(q), q)

    def test_explicit_search_overrides_privacy_guard(self):
        # 用户明确要求搜索时允许 (用户指令优先)
        self.assertTrue(web_search.need_search("帮我搜一下我妈生日礼物推荐"))

    def test_public_realtime_still_searched(self):
        for q in ("今天美元兑人民币汇率多少", "现在几点了"):
            self.assertTrue(web_search.need_search(q), q)


class TestPersonalSpendingRouting(unittest.TestCase):
    """私人消费问句应进 Voicenotes 记忆, 而非公开搜索."""

    def test_spending_questions_are_memory_search(self):
        for q in ("今天我花了多少钱", "昨天我花了多少",
                  "我这个月买了什么东西", "最近我开销大吗"):
            intent, _ = router.classify(q)
            self.assertEqual(intent, router.SEARCH, q)


class TestParseDate(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(web_search.parse_date("2026-10-23"), date(2026, 10, 23))
        self.assertEqual(web_search.parse_date("2026-10-23 07:09"), date(2026, 10, 23))
        self.assertIsNone(web_search.parse_date(""))
        self.assertIsNone(web_search.parse_date(None))
        self.assertIsNone(web_search.parse_date("昨天"))


class TestFilterFresh(unittest.TestCase):
    TODAY = date(2026, 10, 25)

    def _r(self, d):
        return {"publish_date": d, "title": "t", "content": "c", "media": "m", "link": "l"}

    def test_freshness_window(self):
        results = [self._r("2026-10-24"), self._r("2026-10-18"),
                   self._r("2026-10-17"),   # 8天前: 超出7天窗口
                   self._r("2023-01-01"), self._r("")]
        fresh = web_search.filter_fresh(results, days=7, today=self.TODAY)
        self.assertEqual(len(fresh), 2)

    def test_no_date_not_fresh(self):
        # 无日期结果不得作为"最新数据"依据
        fresh = web_search.filter_fresh([self._r("")], days=7, today=self.TODAY)
        self.assertEqual(fresh, [])


class TestFreshWindow(unittest.TestCase):
    def test_market_questions_wider_window(self):
        self.assertEqual(web_search.fresh_window("美元兑人民币汇率多少"), 14)
        self.assertEqual(web_search.fresh_window("黄金行情怎么样"), 14)

    def test_other_questions_7_days(self):
        self.assertEqual(web_search.fresh_window("最新AI新闻"), 7)
        self.assertEqual(web_search.fresh_window("今天天气"), 7)


class TestBuildContext(unittest.TestCase):
    def test_includes_dates_and_sources(self):
        results = [{"publish_date": "2026-10-24", "media": "钛媒体",
                    "title": "离岸人民币走势", "content": "涨27个基点", "link": "https://x"}]
        context, sources = web_search.build_context("人民币汇率", results, today=date(2026, 10, 25))
        self.assertIn("2026-10-24", context)
        self.assertIn("钛媒体", context)
        self.assertEqual(len(sources), 1)


class TestRouterDeviceQuestions(unittest.TestCase):
    """v0.3: 设备故障类问句不得误入记忆检索."""

    def test_device_troubleshooting_is_general(self):
        for q in ("我的AirPods为什么连不上", "我的手机打不开怎么办",
                  "我的手表怎么重启", "我的电脑很卡是什么原因"):
            intent, _ = router.classify(q)
            self.assertEqual(intent, router.GENERAL, q)

    def test_memory_questions_still_routed(self):
        for q in ("我的车停在哪里？", "我的待办有哪些", "我的消费记录",
                  "我妈生日是什么时候？", "我的航班是几点的？",
                  "查一下我的笔记", "我上次说了什么"):
            intent, _ = router.classify(q)
            self.assertEqual(intent, router.SEARCH, q)


class TestTimezoneBounds(unittest.TestCase):
    def test_local_day_to_utc(self):
        from voicenotes_client import _day_bounds_utc
        # 东八区 2026-10-25 当天 = UTC 2026-10-24T16:00 ~ 2026-10-25T16:00
        start, end = _day_bounds_utc("2026-10-25", tz=timezone(timedelta(hours=8)))
        self.assertEqual(start, "2026-10-24T16:00:00Z")
        self.assertEqual(end, "2026-10-25T16:00:00Z")

    def test_utc_day_identity(self):
        from voicenotes_client import _day_bounds_utc
        start, end = _day_bounds_utc("2026-10-25", tz=timezone.utc)
        self.assertEqual(start, "2026-10-25T00:00:00Z")
        self.assertEqual(end, "2026-10-26T00:00:00Z")


class TestCreateDedupe(unittest.TestCase):
    """创建防重复: 已存在相同内容时不再写入."""

    def test_duplicate_content_skips_create(self):
        import server

        class FakeClient:
            def __init__(self):
                self.created = []

            def search(self, q):
                return "Found 1: **车辆停车位置：B2层C36** … %s" % q

            def create_note(self, text, tags=None):
                self.created.append(text)
                return "created"

        fake = FakeClient()
        server._vn_client = fake
        server._vn_client_failed = False
        server.VN_TOKEN = "x"
        server.VN_ENABLED = "on"
        try:
            reply, provider = server.handle_create("我的车停在B2层C36")
            self.assertIn("已经记过", reply)
            self.assertEqual(provider, "create_dup")
            self.assertEqual(fake.created, [])  # 关键: 没有重复写入
        finally:
            server._vn_client = None
            server._vn_client_failed = False
            server.VN_TOKEN = ""
            server.VN_ENABLED = "auto"

    def test_unknown_write_checks_first(self):
        import voicenotes_client as vn

        class FlakyClient:
            def __init__(self):
                self.tried_create = False

            def search(self, q):
                if not self.tried_create:
                    raise vn.VoicenotesUnavailable("无法连接 Voicenotes 服务(网络异常)")
                return "记录: %s" % q  # 核实时发现其实写进去了

            def create_note(self, text, tags=None):
                self.tried_create = True
                raise vn.VoicenotesUnavailable("无法连接 Voicenotes 服务(网络异常)")

        import server
        fake = FlakyClient()
        server._vn_client = fake
        server._vn_client_failed = False
        server.VN_TOKEN = "x"
        server.VN_ENABLED = "on"
        try:
            reply, provider = server.handle_create("测试内容ABC123")
            self.assertIn("已经记好", reply)
        finally:
            server._vn_client = None
            server._vn_client_failed = False
            server.VN_TOKEN = ""
            server.VN_ENABLED = "auto"


class TestRealtimeHandlerFallback(unittest.TestCase):
    def test_search_failure_is_honest(self):
        import server

        def boom(query, **kw):
            raise web_search.SearchError("搜索服务超时或网络异常")

        orig = web_search.search
        web_search.search = boom
        server.GLM_KEY = "k"
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertIn("没拿到可靠的最新数据", reply)
            self.assertEqual(provider, "search_error")
        finally:
            web_search.search = orig

    def test_stale_results_rejected(self):
        import server
        stale = [{"publish_date": "2023-01-01", "title": "t", "content": "c",
                  "media": "m", "link": "l"}]
        orig = web_search.search
        web_search.search = lambda query, **kw: stale
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertIn("没拿到可靠的最新数据", reply)
            self.assertEqual(provider, "search_stale")
        finally:
            web_search.search = orig


if __name__ == "__main__":
    unittest.main()
