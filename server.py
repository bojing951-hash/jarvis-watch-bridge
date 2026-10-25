#!/usr/bin/env python3
"""Jarvis Watch Bridge v0.2 — Apple Watch 快捷指令到随身AI的HTTP桥.

链路: Watch快捷指令 → Cloudflare Tunnel → 本服务 → {GLM问答 | Voicenotes个人记忆} → 朗读.

v0.2 变更:
- 意图路由: general_question / memory_search / memory_create / memory_summary
- Voicenotes MCP 接入 (检索/创建/日期总结), 不可用时诚实降级
- 隐私: 日志不再记录用户问题正文
- 健壮性: 输入长度限制 / 统一错误响应 / 模型调用指标 (GET /metrics)
- glm_provider: 不再把 reasoning_content 当作答案朗读; 错误信息不含内部配置

安全设计:
- 只监听 127.0.0.1; /ask 与 /metrics 均需 X-Jarvis-Key 常数时间比对
- 不记录问题正文、令牌或密钥; 无远程命令执行能力
"""
import hmac
import json
import os
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))

import demo_provider
import glm_provider
import router
import voicenotes_client


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
METRICS = {
    "requests": 0, "ok": 0, "failed": 0,
    "intents": {}, "model_calls": 0, "model_errors": 0,
    "lat_ms": [],
}
_vn_client = None
_vn_client_failed = False


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

def glm_answer(prompt, system=None):
    """调用 GLM 并计入指标; 失败抛 ProviderError(信息不含敏感配置)."""
    METRICS["model_calls"] += 1
    started = time.monotonic()
    try:
        reply = glm_provider.answer(prompt, GLM_KEY, GLM_MODEL, GLM_BASE, GLM_TIMEOUT,
                                    system=system)
        return reply
    except glm_provider.ProviderError as e:
        METRICS["model_errors"] += 1
        raise
    finally:
        ms = (time.monotonic() - started) * 1000
        log("model call %dms" % ms)


GROUNDED_SYSTEM = (
    "你是随身语音助手Jarvis，根据用户的个人记录回答。规则："
    "1.只依据给出的记录内容回答，记录里没有的信息绝对不要编造;"
    "2.查不到就明说'记录里没有找到';3.结论先行,100字以内,中文口语;"
    "4.不要Markdown、列表或表情;5.你没有联网能力,不得声称已查询最新信息。"
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

def handle_create(content):
    client = vn_client()
    if client is None:
        return MEMORY_DEGRADE, "degraded"
    try:
        result = client.create_note(content)
    except voicenotes_client.VoicenotesUnavailable as e:
        log("Voicenotes unavailable: %s" % e)
        return ("抱歉，刚才没能保存到Voicenotes（%s）。"
                "请稍后再试，或者直接用Voicenotes记。" % e), "error"
    note = "记好了。" + (result.strip()[:80] if result and result.strip() != content else "")
    return note, router.CREATE


def handle_search(question):
    client = vn_client()
    if client is None:
        return MEMORY_DEGRADE, "degraded"
    try:
        records = client.search(question)
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
        records = client.list_notes(date_from, date_to)
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
    # general_question
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
    return {
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": uptime,
        "requests": total,
        "ok": METRICS["ok"],
        "failed": METRICS["failed"],
        "success_rate": round(METRICS["ok"] / total, 4) if total else None,
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
        "p95_latency_ms": pct(0.95),
        "max_latency_ms": lat[-1] if lat else None,
        "intents": dict(METRICS["intents"]),
        "model_calls": METRICS["model_calls"],
        "model_errors": METRICS["model_errors"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisWatchBridge/0.2"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        key = self.headers.get("X-Jarvis-Key", "")
        return bool(TOKEN) and hmac.compare_digest(
            key.encode("utf-8"), TOKEN.encode("utf-8"))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "provider": active_provider(),
                "model": GLM_MODEL if active_provider() == "glm" else None,
                "memory": "enabled" if memory_enabled() else "disabled",
                "version": "0.2",
            })
        elif path == "/metrics":
            if not self._authorized():
                log("metrics auth rejected from %s" % self.client_address[0])
                return self._json(401, {"error": "unauthorized"})
            self._json(200, metrics_snapshot())
        elif path == "/":
            self._json(200, {"service": "Jarvis Watch Bridge",
                             "endpoints": ["GET /health", "GET /metrics", "POST /ask"]})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/ask":
            return self._json(404, {"error": "not_found"})
        if not self._authorized():
            log("auth rejected from %s" % self.client_address[0])
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return self._json(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return self._json(400, {"error": "invalid_json"})
        question = str(data.get("question", "")).strip()
        session = str(data.get("session", "default"))
        if not question:
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return self._json(400, {"error": "empty_question"})
        if len(question) > MAX_QUESTION_CHARS:
            METRICS["requests"] += 1
            METRICS["failed"] += 1
            return self._json(400, {
                "error": "question_too_long",
                "reply": "这个问题有点长，Jarvis一次最多听五百字，请精简一下再问。",
            })

        started = time.monotonic()
        try:
            reply, provider, intent = ask(question, session)
        except Exception:  # 兜底: 任何意外异常也要让手表听到一句可朗读的话
            log("internal_error session=%s" % session)
            reply, provider, intent = "贾维斯内部出错了，请稍后再试一次。", "error", "unknown"
        ms = (time.monotonic() - started) * 1000
        METRICS["requests"] += 1
        METRICS["ok"] += 1
        METRICS["lat_ms"].append(ms)
        METRICS["intents"][intent] = METRICS["intents"].get(intent, 0) + 1
        # 隐私: 只记录会话/意图/来源/耗时/字数, 不记录问题正文
        log("session=%s intent=%s provider=%s %.1fms len=%d"
            % (session, intent, provider, ms, len(question)))
        self._json(200, {"reply": reply, "provider": provider, "intent": intent})

    def log_message(self, fmt, *args):
        pass


def main():
    if not TOKEN:
        print("缺少 JARVIS_TOKEN: 请先运行 python3 setup.py 生成 .env", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log("Jarvis Watch Bridge v0.2 启动 http://%s:%d provider=%s memory=%s model=%s"
        % (HOST, PORT, active_provider(),
           "on" if memory_enabled() else "off",
           GLM_MODEL if active_provider() == "glm" else "-"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务已停止")


if __name__ == "__main__":
    main()
