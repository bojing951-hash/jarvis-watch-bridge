"""真实 GLM 调用测试: 仅当 .env 配置了 GLM_API_KEY 时执行, 否则跳过.

注意: 该测试验证的是通用 API (open.bigmodel.cn/api/paas/v4),
不是 GLM Coding Plan 编程接口.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glm_provider
import server

HAS_KEY = bool(server.GLM_KEY)


@unittest.skipUnless(HAS_KEY, "未配置 GLM_API_KEY, 跳过真实模型调用")
class TestRealGlmCall(unittest.TestCase):
    def test_real_question_gets_chinese_answer(self):
        reply = glm_provider.answer(
            "用一句话说明什么是美联储逆回购",
            server.GLM_KEY, server.GLM_MODEL, server.GLM_BASE, server.GLM_TIMEOUT,
        )
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 5)
        # 回答应为可直接朗读的中文, 不含 Markdown 标记
        for marker in ("#", "*", "`"):
            self.assertNotIn(marker, reply)


if __name__ == "__main__":
    unittest.main()
