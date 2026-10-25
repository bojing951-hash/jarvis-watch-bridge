"""意图路由: 把一句话分到 general_question / memory_search / memory_create / memory_summary.

设计原则:
- 纯规则、确定性、可单测, 不引入 Agent 框架
- 创建意图不明确时返回 NEED_CONFIRM, 由 server 转成确认提问, 避免错误写入
- 个人记忆问题绝不落到 general_question 之外的编造路径
"""
import re

GENERAL = "general_question"
SEARCH = "memory_search"
CREATE = "memory_create"
SUMMARY = "memory_summary"
NEED_CONFIRM = "need_confirm"

# 创建意图: 明确的记录指令开头 (可带"贾维斯"前缀)
_CREATE_RE = re.compile(
    r"^(?:贾维斯|jarvis)?[，,。!！\s]*"
    r"(记一下|记住|帮我记住|帮我记|记录一下|记个|记一则|存一下|备忘一下|添加一条记录|新建一条记录)",
    re.IGNORECASE,
)

# 总结意图: 明确要求汇总 + 日期范围词
_SUMMARY_RE = re.compile(
    r"(总结|汇总|回顾|整理)一下?(今天|昨天|前天|今天一天|本周|这周|上周|过去?七天|最近七天|最近一周|这两天)?"
    r"的?(记录|事情|做的事|做了什么|笔记|想法|一切)?.*$"
)
_SUMMARY_DATE_RE = re.compile(r"今天|昨天|前天|本周|这周|上周|过去七天|最近七天|最近一周|这两天")

# 检索意图: 个人记录指向的疑问.
# 注意: 裸"我的"会误伤设备故障类问句("我的AirPods连不上"),
# 因此要求 我的+记忆类名词, 或亲属指涉, 或私人数据问法.
_SEARCH_HINT_RE = re.compile(
    r"我(之前|上次|最近|前面)?(记|说|提|存)过?|有没有(记录|说过|提过)"
    r"|车停在哪|停(在哪|哪里|在哪儿)|我(关于|对).{0,12}(想法|记录|笔记)"
    r"|(查|搜|找|翻)一下(我的?(记录|笔记|备忘|待办|消费)|我)"
    r"|我的(待办|消费|支出|开销|想法|笔记|记录|备忘|计划|账号|密码|生日|纪念日|航班|车牌)"
    r"|我(妈|爸|老婆|老公|对象|媳妇|儿子|女儿|孩子|领导)"
    r"|(生日|纪念日|密码|电话|地址|车牌号?|航班|火车票).{0,6}(是多少|是啥|什么时候|哪天|几号)",
    re.IGNORECASE,
)


def classify(question):
    """返回 (intent, payload). payload: CREATE 时为去掉指令词后的正文."""
    q = (question or "").strip()
    if not q:
        return GENERAL, None

    m = _CREATE_RE.match(q)
    if m:
        content = q[m.end():].strip(" ，,。.!！")
        content = re.sub(r"^(说|道|：|:)+", "", content).strip()
        if len(content) < 4:
            return NEED_CONFIRM, None  # 正文太短, 先确认再写入
        return CREATE, content

    if _SUMMARY_RE.search(q) and _SUMMARY_DATE_RE.search(q):
        return SUMMARY, _summary_range(q)

    if _SEARCH_HINT_RE.search(q):
        return SEARCH, None

    return GENERAL, None


def _summary_range(question):
    """把日期词解析成 (from_iso, to_iso) 相对今天; 未识别默认今天."""
    from datetime import date, timedelta
    today = date.today()
    q = question
    if "昨天" in q:
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if "前天" in q:
        d = today - timedelta(days=2)
        return d.isoformat(), d.isoformat()
    if "上周" in q:
        monday = today - timedelta(days=today.weekday() + 7)
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if ("本周" in q or "这周" in q or "最近一周" in q or "过去七天" in q
            or "最近七天" in q or "这两天" in q):
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    return today.isoformat(), today.isoformat()
