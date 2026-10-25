"""智谱联网搜索模块 (独立 Web Search API, 与模型解耦).

端点: POST {GLM_BASE}/web_search  认证: Bearer GLM_API_KEY
引擎: search_pro (0.03元/次, 多引擎协作) — 仅在路由判定需要联网时调用.

设计:
- need_search(question) 判定是否需要联网(时效词/行情/新闻/明确搜索指令)
- search(query) 返回结构化结果(标题/摘要/来源/日期/链接)
- filter_fresh(results, days) 按发布日期过滤, 旧数据不得用于"当前/最新"类回答
- 不编造: 拿不到新鲜结果时如实上抛, 由上层诚实告知用户
- 独立封装: 未来可整体替换搜索供应商而不动 server
"""
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

# 直连 opener: 绕开本机 Clash 系统代理对 urllib 的拦截 (与 glm_provider 同坑)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DEFAULT_ENGINE = "search_pro"
DEFAULT_COUNT = 6
DEFAULT_TIMEOUT = 10.0  # 典型2-3s; 上限压住, 给上层重试留时间预算

# 触发联网的时效/实时词 (问的是"现在的状态"而非"概念")
_TIME_RE = re.compile(
    r"现在|目前|当前|今天|今日|昨天|本周|刚刚|最新|最近|实时|几点|现在几点"
    r"|涨了?|跌了?|升值|贬值|行情|汇率|股价|币价|价格|多少钱|几块"
    r"|新闻|政策|发布|上线|出台|公布|发布会",
    re.IGNORECASE,
)
# 用户明确要求搜索
_EXPLICIT_RE = re.compile(r"帮我搜|搜索一下|搜一下|上网搜|上网查|网上查|查一下最新", re.IGNORECASE)
# 概念解释类, 即使含时间词也不联网 (例如"今天讲的通货膨胀是什么")
_CONCEPT_RE = re.compile(r"什么是|是什么意思|怎么理解|解释一下|定义是", re.IGNORECASE)
# 隐私护栏: 带私人指涉的问题不得发给公开搜索服务 (用户明确要求搜索除外)
_PERSONAL_RE = re.compile(
    r"我的|我(花了|花掉|买了|买过|记过|说过|存过|提过)"
    r"|我(妈|爸|老婆|老公|对象|媳妇|儿子|女儿|孩子|领导)",
    re.IGNORECASE,
)


class SearchError(Exception):
    """搜索服务不可用或返回异常 — 上层应诚实降级, 不得编造."""


def need_search(question):
    """判定该问题是否需要联网: 只有明确要求或带时效指涉才联网.

    顺序即优先级: 明确搜索 > 隐私护栏(不外发) > 概念题(不联网) > 时效词.
    """
    q = (question or "").strip()
    if not q:
        return False
    if _EXPLICIT_RE.search(q):
        return True
    if _PERSONAL_RE.search(q):
        return False  # 私人指涉的问题绝不发往公开搜索
    if _CONCEPT_RE.search(q):
        # 概念题: 默认不联网, 除非时效词直接修饰行情(如"今天的汇率")
        return bool(re.search(r"行情|汇率|股价|价格|新闻", q))
    return bool(_TIME_RE.search(q))


# 口语填充词: 进搜索框前去掉, 否则引擎被带偏 (实测对比明显)
_FILLER_RE = re.compile(
    r"贾维斯|jarvis|请问|帮我|麻烦|一下|现在|目前|当前|今天|今日|最新|最近|实时"
    r"|是在|还是|了|呢|吗|啊|吧|么|什么|怎么|有没有|告诉我|多少|的|有|是"
    r"|[，。？！,?!\\s]+",
    re.IGNORECASE,
)

# 行情类问题: 市场周末/节假日休市, 最近数据可能超过7天, 放宽到14天
_MARKET_RE = re.compile(
    r"汇率|行情|股价|币价|美元|人民币|欧元|日元|英镑|黄金|原油|指数|A?股|基金|中间价",
    re.IGNORECASE,
)


def fresh_window(question):
    """新鲜度窗口: 行情类14天(覆盖周末休市), 其他7天."""
    return 14 if _MARKET_RE.search(question or "") else 7


def optimize_query(question, today=None):
    """把口语问句转成高质量搜索词: 去填充词 + 锚定当前年月.

    实测: 裸问句"离岸人民币现在是在升值还是贬值"→0条新鲜结果;
    优化为"离岸人民币 升值 贬值 2026年10月"→4条7天内结果.
    """
    today = today or date.today()
    cleaned = _FILLER_RE.sub(" ", question or "")
    terms = [t for t in cleaned.split() if t]
    if not terms:
        terms = [(question or "").strip()[:20]]
    return " ".join(terms) + " %d年%d月" % (today.year, today.month)


def parse_date(value):
    """从 publish_date 字段解析日期; 解析不出返回 None."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(value).strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def filter_fresh(results, days=7, today=None):
    """保留发布日期在 N 天内的结果; 无日期的结果不作为"新鲜"依据."""
    today = today or date.today()
    horizon = today - timedelta(days=days)
    fresh = []
    for r in results:
        d = parse_date(r.get("publish_date"))
        if d and d >= horizon:
            fresh.append(r)
    return fresh


def search(query, count=DEFAULT_COUNT, engine=DEFAULT_ENGINE,
           base_url="https://open.bigmodel.cn/api/paas/v4", api_key="",
           timeout=DEFAULT_TIMEOUT):
    """调用智谱独立搜索API, 返回结果列表(可能为空). 失败抛 SearchError."""
    if not api_key:
        raise SearchError("未配置搜索所需的 GLM_API_KEY")
    payload = {"search_engine": engine, "search_query": query, "count": count}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/web_search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key,
                 "User-Agent": "JarvisWatchBridge/0.3"},
        method="POST",
    )
    try:
        with DIRECT_OPENER.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SearchError("搜索服务返回HTTP %d" % e.code)
    except (socket.timeout, urllib.error.URLError):
        raise SearchError("搜索服务超时或网络异常")
    except (ValueError, json.JSONDecodeError):
        raise SearchError("搜索服务返回了无法解析的内容")
    out = []
    for r in data.get("search_result") or []:
        out.append({
            "title": (r.get("title") or "").strip(),
            "content": (r.get("content") or "").strip(),
            "media": (r.get("media") or "").strip(),
            "publish_date": (r.get("publish_date") or "").strip(),
            "link": (r.get("link") or "").strip(),
        })
    return out


def build_context(question, results, today=None, max_results=4, snippet_chars=160):
    """把新鲜结果整理成给模型的上下文(带日期来源), 以及审计用的来源清单.

    v0.3.1: 结果条数与摘要长度收紧 — 实测上下文越大, GLM空回答率越高
    且更慢; 取前4条、每条160字已足够支撑简短口语回答.
    """
    today = today or date.today()
    lines = []
    sources = []
    for i, r in enumerate(results[:max_results], 1):
        snippet = re.sub(r"\s+", " ", r["content"])[:snippet_chars]
        lines.append("[{i}] 日期:{d} 来源:{m}\n{t}\n{s}".format(
            i=i, d=r["publish_date"] or "无日期", m=r["media"] or "未知",
            t=r["title"][:60], s=snippet))
        sources.append({"date": r["publish_date"], "media": r["media"],
                        "title": r["title"][:60], "link": r["link"]})
    context = ("用户问题: %s\n\n以下是搜索结果(今天是%s, 只依据这些结果回答):\n%s"
               % (question, today.isoformat(), "\n\n".join(lines)))
    return context, sources
