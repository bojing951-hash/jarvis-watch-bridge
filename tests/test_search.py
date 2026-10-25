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
    def test_search_api_error_distinct(self):
        import server

        def boom(query, **kw):
            raise web_search.SearchError("搜索服务超时或网络异常")

        orig = web_search.search
        web_search.search = boom
        server.GLM_KEY = "k"
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertIn("联网搜索服务", reply)
            self.assertEqual(provider, "search_api_error")
        finally:
            web_search.search = orig

    def test_no_results_after_retry_distinct(self):
        import server
        calls = []
        orig = web_search.search
        web_search.search = lambda query, **kw: calls.append(query) or []
        try:
            reply, provider = server.handle_realtime("某个生僻实时话题")
            self.assertIn("没搜到相关内容", reply)
            self.assertEqual(provider, "search_no_results")
            self.assertEqual(len(calls), 2)  # 带锚点一次 + 去锚点重试一次
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
            self.assertIn("不是最近的数据", reply)
            self.assertEqual(provider, "search_stale")
        finally:
            web_search.search = orig

    def test_transient_timeout_retried_once(self):
        """首次搜索瞬时超时: 用降级查询重试, 成功后正常走回答链路."""
        import glm_provider
        import server
        fresh = [{"publish_date": "2026-10-24", "title": "t", "content": "c",
                  "media": "m", "link": "l"}]
        calls = []

        def flaky(query, **kw):
            calls.append(query)
            if len(calls) == 1:
                raise web_search.SearchError("搜索服务超时或网络异常")
            return fresh

        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = flaky
        server.glm_answer = lambda prompt, system=None, retries=1: "搜到的回答"
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertEqual(reply, "搜到的回答")
            self.assertEqual(provider, "search+glm")
            self.assertEqual(len(calls), 2)
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm

    def test_auth_error_not_retried(self):
        """鉴权类错误重试无意义: 只调一次."""
        import server
        calls = []

        def denied(query, **kw):
            calls.append(query)
            raise web_search.SearchError("搜索服务返回HTTP 401")

        orig = web_search.search
        web_search.search = denied
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertEqual(provider, "search_api_error")
            self.assertEqual(len(calls), 1)
        finally:
            web_search.search = orig

    def test_model_error_after_good_search(self):
        """搜索拿到新鲜数据但GLM连续失败: 不得再说'没拿到数据'."""
        import glm_provider
        import server
        fresh = [{"publish_date": "2026-10-24", "title": "t", "content": "c",
                  "media": "m", "link": "l"}]
        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: fresh
        server.glm_answer = lambda prompt, system=None, retries=1: (
            (_ for _ in ()).throw(glm_provider.ProviderError("GLM返回了空回答")))
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertIn("组织回答", reply)
            self.assertEqual(provider, "model_error")
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm

    def test_slim_context_recovery_after_empties(self):
        """P0: 大上下文连空后, 瘦身上下文(2条/100字)再试一次可救回."""
        import glm_provider
        import server
        fresh = [{"publish_date": "2026-10-24", "title": "标题%d" % i,
                  "content": "内容" * 60, "media": "m", "link": "l%d" % i}
                 for i in range(4)]
        prompts = []

        def fake_glm(prompt, system=None, retries=1):
            prompts.append(len(prompt))
            if len(prompts) <= 1:  # glm_answer 内部自带重试由 retries=0 控制, 这里只模拟: 大上下文必空
                raise glm_provider.ProviderError("GLM返回了空回答")
            if len(prompt) > 700:  # 完整上下文(4条/160字)仍失败
                raise glm_provider.ProviderError("GLM返回了空回答")
            return "瘦身救回的回答"

        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: fresh
        server.glm_answer = fake_glm
        try:
            reply, provider = server.handle_realtime("现在美元汇率多少")
            self.assertEqual(reply, "瘦身救回的回答")
            self.assertEqual(provider, "search+glm")
            self.assertLess(prompts[-1], 700)  # 最后一次确实是瘦身上下文
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm


class TestOptimizeQuery(unittest.TestCase):
    def test_filler_stripped_and_date_anchored(self):
        from datetime import date
        q = web_search.optimize_query("贾维斯，今天有什么重要的AI新闻",
                                      today=date(2026, 10, 25))
        self.assertNotIn("贾维斯", q)
        self.assertNotIn("今天", q)
        self.assertNotIn("有", q)
        self.assertTrue(q.endswith("2026年10月"))
        self.assertIn("AI", q)

    def test_finance_terms_kept(self):
        from datetime import date
        q = web_search.optimize_query("离岸人民币现在是在升值还是贬值",
                                      today=date(2026, 10, 25))
        for term in ("离岸人民币", "升值", "贬值"):
            self.assertIn(term, q)

    def test_english_terms_not_mangled(self):
        """P0回归: 填充词正则曾把 \\s 写成字面's', 吃掉 USD 里的 S."""
        from datetime import date
        q = web_search.optimize_query(
            "最新美元兑离岸人民币 USD/CNH 汇率是多少",
            today=date(2026, 10, 26))
        self.assertIn("USD/CNH", q)

    def test_relative_time_noise_stripped(self):
        """P0回归: '过去24小时''哪些''重要' 这类口语噪声不进查询词."""
        from datetime import date
        q = web_search.optimize_query("过去24小时有哪些重要的AI行业新闻？",
                                      today=date(2026, 10, 26))
        for noise in ("过去24小时", "哪些", "重要"):
            self.assertNotIn(noise, q)
        self.assertIn("AI行业新闻", q)


class TestContentDate(unittest.TestCase):
    """P0: 无日期结果按正文自报日期判定, 不再一律丢弃."""

    TODAY = date(2026, 10, 26)

    def _r(self, content, publish=""):
        return {"publish_date": publish, "title": "t", "content": content,
                "media": "m", "link": "l"}

    def test_chinese_date_in_content(self):
        r = self._r("2026年10月16日美元/人民币报6.7220，下调33点")
        self.assertEqual(web_search.content_date(r, today=self.TODAY),
                         date(2026, 10, 16))

    def test_iso_date_in_content(self):
        r = self._r("出处：北京商报 2026-10-08 离岸人民币盘中升破6.70关口")
        self.assertEqual(web_search.content_date(r, today=self.TODAY),
                         date(2026, 10, 8))

    def test_month_day_assumes_current_year(self):
        r = self._r("10月24日，南京物理AI大会开幕")
        self.assertEqual(web_search.content_date(r, today=self.TODAY),
                         date(2026, 10, 24))

    def test_future_date_rejected(self):
        r = self._r("2026年12月31日预告")
        self.assertIsNone(web_search.content_date(r, today=self.TODAY))

    def test_no_date_none(self):
        self.assertIsNone(web_search.content_date(self._r("没有任何日期"), self.TODAY))

    def test_effective_date_prefers_publish(self):
        r = self._r("2026年10月24日的内容日期", publish="2026-10-19")
        self.assertEqual(web_search.effective_date(r, today=self.TODAY),
                         date(2026, 10, 19))

    def test_undated_with_content_date_counts_fresh(self):
        results = [self._r("2026年10月24日重要数据发布")]
        fresh = web_search.filter_fresh(results, days=7, today=self.TODAY)
        self.assertEqual(len(fresh), 1)

    def test_undated_without_date_still_stale(self):
        # 完全拿不到日期依据: 宁可不用
        results = [self._r("没有提到任何日期的页面")]
        fresh = web_search.filter_fresh(results, days=7, today=self.TODAY)
        self.assertEqual(fresh, [])


class TestSearchRecencyParam(unittest.TestCase):
    """P0: search() 支持官方 search_recency_filter, 且确实放进请求体."""

    def test_recency_in_payload(self):
        import json as _json

        class FakeResp:
            def read(self):
                return _json.dumps({"search_result": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener:
            def __init__(self):
                self.payload = None

            def open(self, req, timeout=None):
                self.payload = _json.loads(req.data.decode())
                return FakeResp()

        fake = FakeOpener()
        orig = web_search.DIRECT_OPENER
        web_search.DIRECT_OPENER = fake
        try:
            web_search.search("测试 查询", api_key="k", recency="oneWeek")
        finally:
            web_search.DIRECT_OPENER = orig
        self.assertEqual(fake.payload.get("search_recency_filter"), "oneWeek")
        self.assertEqual(fake.payload.get("count"), web_search.DEFAULT_COUNT)


class TestSupplementSearch(unittest.TestCase):
    """P0: 行情方向题缺可比基准时, 有目的地补搜一次(去重、有上限)."""

    def _r(self, link, content, publish="2026-10-25"):
        return {"publish_date": publish, "title": "t", "content": content,
                "media": "m", "link": link}

    def test_supplement_fires_without_basis(self):
        import server
        calls = []
        no_basis = [self._r("a", "离岸人民币报6.68，创今年新高")]
        with_basis = [self._r("b", "中间价报6.7208，较前一交易日上调12个基点")]
        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: calls.append(query) or (
            no_basis if len(calls) == 1 else with_basis)
        server.glm_answer = lambda prompt, system=None, retries=1: "回答"
        try:
            reply, provider = server.handle_realtime("离岸人民币在升值还是贬值")
            self.assertEqual(provider, "search+glm")
            self.assertEqual(len(calls), 2)  # 主查询 + 补充一次, 到此为止
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm

    def test_no_supplement_when_basis_present(self):
        import server
        calls = []
        with_basis = [self._r("a", "离岸人民币报6.6817，较前一交易日上涨37个基点")]
        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: calls.append(query) or with_basis
        server.glm_answer = lambda prompt, system=None, retries=1: "回答"
        try:
            reply, provider = server.handle_realtime("离岸人民币在升值还是贬值")
            self.assertEqual(provider, "search+glm")
            self.assertEqual(len(calls), 1)
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm

    def test_supplement_dedupes_by_link(self):
        import server
        calls = []
        only = [self._r("a", "离岸人民币报6.68，没有对比基准")]
        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: calls.append(query) or only
        prompts = []
        server.glm_answer = lambda prompt, system=None, retries=1: prompts.append(1) or "回答"
        try:
            reply, provider = server.handle_realtime("离岸人民币在升值还是贬值")
            self.assertEqual(provider, "search+glm")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(prompts), 1)
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm

    def test_non_direction_question_no_supplement(self):
        import server
        calls = []
        fresh = [self._r("a", "没有基准词的普通新闻内容")]
        orig_search, orig_glm = web_search.search, server.glm_answer
        web_search.search = lambda query, **kw: calls.append(query) or fresh
        server.glm_answer = lambda prompt, system=None, retries=1: "回答"
        try:
            reply, provider = server.handle_realtime("最新的AI新闻有哪些")
            self.assertEqual(provider, "search+glm")
            self.assertEqual(len(calls), 1)
        finally:
            web_search.search = orig_search
            server.glm_answer = orig_glm


if __name__ == "__main__":
    unittest.main()
