#!/usr/bin/env python3
"""Jarvis Watch Bridge v0.3 — Apple Watch 快捷指令到随身AI的HTTP桥.

链路: Watch快捷指令 → Cloudflare Tunnel → 本服务 → {GLM问答 | 联网搜索 | Voicenotes个人记忆} → 朗读.

v0.3 变更:
- POST /ask-text: 纯文本语音接口, 手表端彻底摆脱 JSON/字典解析
- 联网搜索: 智谱独立搜索API + 时效路由, 行情类回答强制新鲜数据与方向规则
- 诊断: 每请求 request_id + 分段耗时(模型/搜索/MCP) + 回答字数, 仍不记录问题正文
- 指标: 区分 HTTP 成功与业务成功(回答成功/降级/失败), 不虚报成功率
- 健壮性: 请求体大小上限(64KB), HTTP/1.1, 截断检测
- 记忆安全: 创建防重复, 写入结果未知时先核实再答复; 本地时区日期边界

安全设计:
- 只监听 127.0.0.1; /ask /ask-text /metrics 均需 X-Jarvis-Key 常数时间比对
- 不记录问题正文、令牌或密钥; 无远程命令执行能力
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))

import demo_provider
import glm_provider
import router
import voicenotes_client
import web_search


def load_env(path):
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    return cfg


ENV = load_env(os.path.join(BASE, ".env"))
HOST = ENV.get("HOST", "127.0.0.1")
PORT = int(ENV.get("PORT", "8765"))
TOKEN = ENV.get("JARVIS_TOKEN", "")
PROVIDER = ENV.get("PROVIDER", "auto")
GLM_KEY = ENV.get("GLM_API_KEY", "")
GLM_MODEL = ENV.get("GLM_MODEL", "glm-5.3-flash")
GLM_BASE = ENV.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_TIMEOUT = float(ENV.get("GLM_TIMEOUT", "45"))
MAX_QUESTION_CHARS = int(ENV.get("MAX_QUESTION_CHARS", "500"))
VN_TOKEN = ENV.get("VOICENOTES_ACCESS_TOKEN", "")
VN_ENABLED = ENV.get("VOICENOTES_ENABLED", "auto")
VN_ENDPOINT = ENV.get("VOICENOTES_ENDPOINT", "https://api.voicenotes.com/mcp")
VN_TIMEOUT = float(ENV.get("VOICENOTES_TIMEOUT", "20"))

STARTED_AT = datetime.now()
MAX_BODY_BYTES = 65536
METRICS = {
    "requests": 0, "ok": 0, "failed": 0,       # HTTP 层
    "answers_ok": 0, "answers_degraded": 0, "answers_failed": 0,  # 业务层
    "intents": {}, "model_calls": 0, "model_errors": 0,
    "search_calls": 0, "search_errors": 0,
    "mcp_calls": 0, "mcp_errors": 0,
    "lat_ms": [],
}
_vn_client = None
_vn_client_failed = False

# 每请求诊断上下文(线程隔离): request_id + 分段耗时, 只记非敏感信息
_CTX = threading.local()


def ctx():
    if not hasattr(_CTX, "data"):
        _CTX.data = {"rid": "-", "model_ms": 0.0, "search_ms": 0.0, "mcp_ms": 0.0}
    return _CTX.data


def new_rid():
    rid = secrets.token_hex(4)
    ctx()["rid"] = rid
    ctx()["model_ms"] = 0.0
    ctx()["search_ms"] = 0.0
    ctx()["mcp_ms"] = 0.0
    return rid


def log(message):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


def active_provider():
    if PROVIDER == "demo":
        return "demo"
    if PROVIDER == "glm":
        return "glm"
    return "glm" if GLM_KEY else "demo"


def memory_enabled():
    if VN_ENABLED == "off":
        return False
    if VN_ENABLED == "on":
        return True
    return bool(VN_TOKEN)


def vn_client():
    """懒加载 Voicenotes 客户端; 未配置/失败时返回 None 并让调用方诚实降级."""
    global _vn_client, _vn_client_failed
    if not memory_enabled() or _vn_client_failed:
        return None
    if _vn_client is None:
        try:
            _vn_client = voicenotes_client.VoicenotesClient(
                VN_ENDPOINT, VN_TOKEN, VN_TIMEOUT)
        except voicenotes_client.VoicenotesUnavailable:
            _vn_client_failed = True
            return None
    return _vn_client


# ---------- GLM 辅助 ----------

def glm_answer(prompt, system=None, retries=1):
    """调用 GLM 并计入指标与分段耗时; 失败抛 ProviderError(不含敏感配置)."""
    METRICS["model_calls"] += 1
    started = time.monotonic()
    try:
        return glm_provider.answer(prompt, GLM_KEY, GLM_MODEL, GLM_BASE, GLM_TIMEOUT,
                                   system=system, retries=retries)
    except glm_provider.ProviderError:
        METRICS["model_errors"] += 1
        raise
    finally:
        ctx()["model_ms"] = (time.monotonic() - started) * 1000


GROUNDED_SYSTEM = (
    "你是随身语音助手Jarvis，根据用户的个人记录回答。规则："
    "1.只依据给出的记录内容回答，记录里没有的信息绝对不要编造;"
    "2.查不到就明说'记录里没有找到';3.结论先行,100字以内,中文口语;"
    "4.不要Markdown、列表或表情;5.你没有联网能力,不得声称已查询最新信息。"
)

SEARCH_SYSTEM = (
    "你是随身语音助手Jarvis，根据给出的联网搜索结果回答用户的实时问题。规则："
    "1.只依据搜索结果回答，结果里没有的数字或事实绝对不要编造;"
    "2.结论先行，120字以内，中文口语，不要Markdown、列表、网址;"
    "3.必须说明数据日期或比较基准（例如'截至X月X日'、'较上一交易日'）;"
    "4.金融方向判断要特别小心：美元兑人民币(USD/CNH)数值上升=人民币贬值，"
    "数值下降=人民币升值，先在结果里找到具体数字再下结论，拿不准就说数据不足;"
    "5.如果搜索结果不足以回答（数据太旧、没有相关数字），直接说明没查到可靠"
    "的最新数据，建议稍后再问，不要用旧数据冒充最新行情。"
)


def grounded_or_raw(question, records_text, fallback_prefix):
    """有 GLM 用模型提炼; 没有 GLM 直接给出截断原文, 都不编造."""
    if active_provider() == "glm":
        prompt = ("用户问题: %s\n\n相关个人记录:\n%s\n\n"
                  "请只根据以上记录回答。" % (question, records_text[:4000]))
        try:
            return glm_answer(prompt, system=GROUNDED_SYSTEM), "voicenotes+glm"
        except glm_provider.ProviderError as e:
            log("GLM error in memory answer: %s" % e)
            # 模型失败不编造, 落回原文摘录
    digest = records_text.strip().replace("\n", "；")
    if len(digest) > 300:
        digest = digest[:300] + "……(内容较长已截断)"
    return "%s：%s" % (fallback_prefix, digest), "voicenotes"


MEMORY_DEGRADE = ("Jarvis的记忆功能还没有连接Voicenotes，"
                  "我暂时查不到你的个人记录。请在电脑上运行授权工具并重启Jarvis。"
                  "普通知识问答不受影响。")


# ---------- 意图处理器 ----------

# 搜索路径失败分类: 不同环节不同话术, 不再用一句话掩盖所有故障
SEARCH_REPLIES = {
    "search_api_error": "联网搜索服务这会儿访问不了，稍后再试一次。",
    "search_no_results": "我联网搜了，但没搜到相关内容，换个说法再试试。",
    "search_stale": "搜到了一些结果，但都不是最近的数据，不敢拿来当最新情况说，稍后再问。",
    "model_error": "资料查到了，但我组织回答时出了点问题，请再问一次。",
}


def audit_search_sources(rid, question, results):
    """检索来源留档(.run/, 本地不外传): 供事后核查, 不含问题正文."""
    try:
        path = os.path.join(BASE, ".run", "search_audit.jsonl")
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "rid": rid,
            "qhash": hashlib.sha1(question.encode("utf-8")).hexdigest()[:10],
            "results": [{"date": r.get("publish_date"), "media": r.get("media"),
                         "title": r.get("title", "")[:60]} for r in results[:6]],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 留档失败不影响回答


def _transient_search_error(err):
    """瞬时类搜索错误(超时/网络/5xx/429)可重试; 鉴权类重试无意义."""
    msg = str(err)
    if "HTTP 4" in msg and "HTTP 429" not in msg:
        return False
    return True


def handle_realtime(question):
    """需要实时信息的问题: 搜索→过滤新鲜结果→模型组织口语回答.

    失败分类(服务端可区分, 话术不同):
    search_api_error  搜索接口异常(瞬时错误重试一次后仍失败)
    search_no_results 两次查询均无结果
    search_stale      有结果但全超出新鲜度窗口
    model_error       搜索成功但GLM连续空回答/报错
    """
    METRICS["search_calls"] += 1
    query = web_search.optimize_query(question)
    bare = query.rsplit(" ", 1)[0]  # 去日期锚点的降级查询
    started = time.monotonic()
    results = []
    first_error = None
    for attempt, q in enumerate((query, bare)):
        try:
            results = web_search.search(q, api_key=GLM_KEY)
        except web_search.SearchError as e:
            METRICS["search_errors"] += 1
            log("search error (attempt %d): %s" % (attempt + 1, e))
            if first_error is None:
                first_error = e
            # 鉴权类错误不重试; 瞬时类重试一次(换降级查询)
            if not _transient_search_error(e):
                break
            continue
        if results:
            if attempt == 1:
                log("search retry succeeded: %d results" % len(results))
            break
        log("search attempt %d: 0 results" % (attempt + 1))
    ctx()["search_ms"] = (time.monotonic() - started) * 1000
    if not results:
        if first_error is not None:
            return SEARCH_REPLIES["search_api_error"], "search_api_error"
        return SEARCH_REPLIES["search_no_results"], "search_no_results"
    audit_search_sources(ctx()["rid"], question, results)
    days = web_search.fresh_window(question)  # 行情类14天(周末休市), 其他7天
    fresh = web_search.filter_fresh(results, days=days)
    log("search: %d results, %d fresh (%dd)" % (len(results), len(fresh), days))
    if not fresh:
        # 有结果但全旧: 不拿旧数据冒充最新, 诚实告知
        return SEARCH_REPLIES["search_stale"], "search_stale"
    context, _sources = web_search.build_context(question, fresh)
    try:
        # 搜索已付费拿到数据, 对GLM空回答多一次重试(共3次尝试)
        reply = glm_answer(context, system=SEARCH_SYSTEM, retries=2)
        return reply, "search+glm"
    except glm_provider.ProviderError as e:
        log("GLM error after search: %s" % e)
        return SEARCH_REPLIES["model_error"], "model_error"


def _mcp_op(fn, *args):
    """Voicenotes 调用统一计时/计数包装."""
    METRICS["mcp_calls"] += 1
    started = time.monotonic()
    try:
        return fn(*args)
    except voicenotes_client.VoicenotesUnavailable:
        METRICS["mcp_errors"] += 1
        raise
    finally:
        ctx()["mcp_ms"] = (time.monotonic() - started) * 1000


def handle_create(content):
    client = vn_client()
    if client is None:
        return MEMORY_DEGRADE, "degraded"
    # 防重复: 用户重复下达同一记录指令(如上次朗读失败后再说一次)时不重复写入
    try:
        found = _mcp_op(client.search, content)
        if found and content in found:
            return "这条刚才已经记过了，没有重复记录。", "create_dup"
    except voicenotes_client.VoicenotesUnavailable:
        pass  # 查重失败不阻塞创建, 继续尝试写入
    try:
        result = _mcp_op(client.create_note, content)
    except voicenotes_client.VoicenotesUnavailable as e:
        log("Voicenotes unavailable: %s" % e)
        msg = str(e)
        if "网络异常" in msg or "连接中断" in msg or "HTTP 5" in msg or "超时" in msg:
            # 写入结果未知: 先核实是否其实已保存, 再决定怎么答复
            try:
                found = _mcp_op(client.search, content)
                if found and content in found:
                    return "刚才那条其实已经记好了，放心。", "create_dup"
            except voicenotes_client.VoicenotesUnavailable:
                pass
            return ("刚才保存时网络出了问题，我不确定记没记上。"
                    "请再对我说一次这条内容，我会先查重不会重复记录。"), "error"
        return ("抱歉，刚才没能保存到Voicenotes（%s）。"
                "请稍后再试，或者直接用Voicenotes记。" % e), "error"
    note = "记好了。" + (result.strip()[:80] if result and result.strip() != content else "")
    return note, router.CREATE


def handle_search(question):
    client = vn_client()
    if client is None:
        return MEMORY_DEGRADE, "degraded"
    try:
        records = _mcp_op(client.search, question)
    except voicenotes_client.VoicenotesUnavailable as e:
        log("Voicenotes unavailable: %s" % e)
        return ("抱歉，现在连不上Voicenotes，查不了你的记录。稍后再试。"), "error"
    if not records or not records.strip():
        return "我在Voicenotes里没有找到相关记录。", router.SEARCH
    return grounded_or_raw(question, records, "找到的记录如下")


def handle_summary(date_range):
    client = vn_client()
    if client is None:
        return MEMORY_DEGRADE, "degraded"
    date_from, date_to = date_range or (None, None)
    try:
        records = _mcp_op(client.list_notes, date_from, date_to)
    except voicenotes_client.VoicenotesUnavailable as e:
        log("Voicenotes unavailable: %s" % e)
        return ("抱歉，现在连不上Voicenotes，做不了总结。稍后再试。"), "error"
    if not records or not records.strip():
        return "这个时间段你在Voicenotes里还没有记录。", router.SUMMARY
    return grounded_or_raw("总结一下这些记录的要点", records,
                           "%s至%s的记录摘要" % (date_from, date_to))


def ask(question, session="default"):
    """统一入口: 路由意图 → 处理 → (reply, provider, intent)."""
    intent, payload = router.classify(question)
    if intent == router.NEED_CONFIRM:
        return ("好的，要记什么？请再说一次内容，比如：记一下，车停在B2层C36。"), "jarvis", intent
    if intent == router.CREATE:
        reply, provider = handle_create(payload)
        return reply, provider, intent
    if intent == router.SEARCH:
        reply, provider = handle_search(question)
        return reply, provider, intent
    if intent == router.SUMMARY:
        reply, provider = handle_summary(payload)
        return reply, provider, intent
    # general_question: 先判断是否需要实时信息
    if web_search.need_search(question) and active_provider() == "glm":
        reply, provider = handle_realtime(question)
        return reply, provider, intent
    if active_provider() == "demo":
        return demo_provider.answer(question), "demo", intent
    try:
        reply = glm_answer(question)
        return reply, "glm", intent
    except glm_provider.ProviderError as e:
        log("GLM error: %s" % e)
        return ("抱歉，Jarvis的大脑暂时不可用，请稍后再试。"), "error", intent


# ---------- HTTP ----------

def metrics_snapshot():
    lat = sorted(METRICS["lat_ms"])
    total = METRICS["requests"]
    def pct(p):
        if not lat:
            return None
        return lat[min(len(lat) - 1, int(len(lat) * p))]
    uptime = int((datetime.now() - STARTED_AT).total_seconds())
    answers = METRICS["answers_ok"] + METRICS["answers_degraded"] + METRICS["answers_failed"]
    return {
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": uptime,
        "requests": total,
        "http_ok": METRICS["ok"],
        "http_failed": METRICS["failed"],
        "http_success_rate": round(METRICS["ok"] / total, 4) if total else None,
        # 业务层: HTTP 200 但回答降级/失败不计入成功, 不虚报
        "answers_ok": METRICS["answers_ok"],
        "answers_degraded": METRICS["answers_degraded"],
        "answers_failed": METRICS["answers_failed"],
        "answer_success_rate": round(METRICS["answers_ok"] / answers, 4) if answers else None,
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
        "p95_latency_ms": pct(0.95),
        "max_latency_ms": lat[-1] if lat else None,
        "intents": dict(METRICS["intents"]),
        "model_calls": METRICS["model_calls"],
        "model_errors": METRICS["model_errors"],
        "search_calls": METRICS["search_calls"],
        "search_errors": METRICS["search_errors"],
        "mcp_calls": METRICS["mcp_calls"],
        "mcp_errors": METRICS["mcp_errors"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisWatchBridge/0.3"
    protocol_version = "HTTP/1.1"  # 每个响应都带准确 Content-Length, 可安全启用

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Jarvis-Request-Id", ctx()["rid"])
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _text(self, code, text):
        # 纯文本接口: 手表端拿到的就是可朗读内容, 无任何结构需要解析
        self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _authorized(self):
        key = self.headers.get("X-Jarvis-Key", "")
        return bool(TOKEN) and hmac.compare_digest(
            key.encode("utf-8"), TOKEN.encode("utf-8"))

    def do_GET(self):
        new_rid()
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "provider": active_provider(),
                "model": GLM_MODEL if active_provider() == "glm" else None,
                "memory": "enabled" if memory_enabled() else "disabled",
                "search": "enabled" if (active_provider() == "glm" and GLM_KEY) else "disabled",
                "version": "0.3",
            })
        elif path == "/metrics":
            if not self._authorized():
                log("rid=%s metrics auth rejected" % ctx()["rid"])
                return self._json(401, {"error": "unauthorized"})
            self._json(200, metrics_snapshot())
        elif path == "/":
            self._json(200, {"service": "Jarvis Watch Bridge",
                             "endpoints": ["GET /health", "GET /metrics",
                                           "POST /ask (JSON)", "POST /ask-text (纯文本)"]})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        rid = new_rid()
        path = self.path.split("?")[0]
        plain = (path == "/ask-text")
        if path not in ("/ask", "/ask-text"):
            return self._json(404, {"error": "not_found"})

        def respond_err(code, err, speak):
            # JSON 客户端保持结构化错误; 纯文本客户端只给可朗读的话
            if plain:
                self._text(code, speak)
            else:
                self._json(code, {"error": err, "reply": speak})

        if not self._authorized():
            log("rid=%s auth rejected" % rid)
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return respond_err(401, "unauthorized",
                               "访问令牌不对，请检查快捷指令里的X-Jarvis-Key。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return respond_err(413, "body_too_large",
                               "请求太大了，Jarvis听不下这么长的内容。")
        try:
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return respond_err(400, "invalid_json",
                               "请求格式有问题，Jarvis没听懂，请重试。")
        question = str(data.get("question", "")).strip()
        session = str(data.get("session", "default"))
        if not question:
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return respond_err(400, "empty_question",
                               "Jarvis没听到问题，请再说一次。")
        if len(question) > MAX_QUESTION_CHARS:
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return respond_err(400, "question_too_long",
                               "这个问题有点长，Jarvis一次最多听五百字，请精简一下再问。")

        started = time.monotonic()
        try:
            reply, provider, intent = ask(question, session)
        except Exception:  # 兜底: 任何意外异常也要让手表听到一句可朗读的话
            log("rid=%s internal_error" % rid)
            reply, provider, intent = "贾维斯内部出错了，请稍后再试一次。", "error", "unknown"
        ms = (time.monotonic() - started) * 1000
        METRICS["requests"] += 1
        METRICS["ok"] += 1
        METRICS["lat_ms"].append(ms)
        METRICS["intents"][intent] = METRICS["intents"].get(intent, 0) + 1
        # 业务层成败: 降级(记忆/搜索不可用)与失败分开统计, 不虚报成功率
        if provider in ("error", "search_api_error", "search_no_results",
                        "search_stale", "model_error"):
            METRICS["answers_failed"] += 1
        elif provider in ("degraded", "demo"):
            METRICS["answers_degraded"] += 1
        else:
            METRICS["answers_ok"] += 1
        # 隐私: 只记会话/意图/来源/分段耗时/字数, 不记录问题正文
        log("rid=%s session=%s intent=%s provider=%s http=200 "
            "model=%.0fms search=%.0fms mcp=%.0fms total=%.1fms "
            "qchars=%d rchars=%d"
            % (rid, session, intent, provider, ctx()["model_ms"],
               ctx()["search_ms"], ctx()["mcp_ms"], ms,
               len(question), len(reply)))
        if plain:
            self._text(200, reply)
        else:
            self._json(200, {"reply": reply, "provider": provider, "intent": intent})

    def log_message(self, fmt, *args):
        pass


def main():
    if not TOKEN:
        print("缺少 JARVIS_TOKEN: 请先运行 python3 setup.py 生成 .env", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log("Jarvis Watch Bridge v0.3 启动 http://%s:%d provider=%s memory=%s search=%s model=%s"
        % (HOST, PORT, active_provider(),
           "on" if memory_enabled() else "off",
           "on" if GLM_KEY else "off",
           GLM_MODEL if active_provider() == "glm" else "-"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务已停止")


if __name__ == "__main__":
    main()
