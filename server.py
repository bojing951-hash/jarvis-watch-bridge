#!/usr/bin/env python3
"""Jarvis Watch Bridge — Apple Watch 快捷指令到本机 AI 的 HTTP 桥.

链路: Apple Watch 快捷指令 → iPhone/互联网 → Cloudflare Tunnel → 本服务 → GLM → 朗读.

安全设计:
- 只监听 127.0.0.1, 不直接暴露到局域网/公网
- /ask 必须携带 X-Jarvis-Key 请求头, 与 .env 中 JARVIS_TOKEN 常数时间比对
- 不记录任何密钥内容到日志; 不提供远程命令执行能力
"""
import hmac
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))

import demo_provider
import glm_provider


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


def log(message):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


def active_provider():
    if PROVIDER == "demo":
        return "demo"
    if PROVIDER == "glm":
        return "glm"
    return "glm" if GLM_KEY else "demo"


def ask(question):
    """返回 (reply, provider). 模型失败时返回可口播的友好错误而非抛异常,
    保证手表端朗读动作总能拿到文本."""
    provider = active_provider()
    if provider == "demo":
        return demo_provider.answer(question), "demo"
    try:
        reply = glm_provider.answer(question, GLM_KEY, GLM_MODEL, GLM_BASE, GLM_TIMEOUT)
        return reply, "glm"
    except glm_provider.ProviderError as e:
        log("GLM error: %s" % e)
        return "抱歉，Jarvis的大脑暂时不可用：%s。请稍后再试。" % e, "error"


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisWatchBridge/0.1"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            provider = active_provider()
            self._json(200, {
                "status": "ok",
                "provider": provider,
                "model": GLM_MODEL if provider == "glm" else None,
            })
        elif path == "/":
            self._json(200, {"service": "Jarvis Watch Bridge",
                             "endpoints": ["GET /health", "POST /ask"]})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/ask":
            return self._json(404, {"error": "not_found"})
        key = self.headers.get("X-Jarvis-Key", "")
        if not TOKEN or not hmac.compare_digest(key.encode("utf-8"), TOKEN.encode("utf-8")):
            log("auth rejected from %s" % self.client_address[0])
            return self._json(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid_json"})
        question = str(data.get("question", "")).strip()
        session = str(data.get("session", "default"))
        if not question:
            return self._json(400, {"error": "empty_question"})
        reply, provider = ask(question)
        log("session=%s provider=%s q=%.60s" % (session, provider, question))
        self._json(200, {"reply": reply, "provider": provider})

    def log_message(self, fmt, *args):
        pass  # 用自定义 log(), 不输出默认访问日志


def main():
    if not TOKEN:
        print("缺少 JARVIS_TOKEN: 请先运行 python3 setup.py 生成 .env", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    provider = active_provider()
    log("Jarvis Watch Bridge 启动 http://%s:%d provider=%s model=%s"
        % (HOST, PORT, provider, GLM_MODEL if provider == "glm" else "-"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务已停止")


if __name__ == "__main__":
    main()
