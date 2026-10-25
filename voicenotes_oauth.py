#!/usr/bin/env python3
"""Voicenotes MCP OAuth 授权工具 (浏览器一键授权, 令牌写回本地 .env).

流程 (MCP/OAuth 2.0 标准):
1. 发现授权服务器: /.well-known/oauth-protected-resource -> /.well-known/oauth-authorization-server
2. 动态客户端注册 (RFC 7591, 无需手动 client_id/secret)
3. PKCE + 本地回环接收授权码, 自动打开浏览器
4. 换取访问令牌, 写入 .env 的 VOICENOTES_ACCESS_TOKEN (不打印)

用法: python3 voicenotes_oauth.py
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")
MCP_URL = "https://api.voicenotes.com/mcp"
ORIGIN = "https://api.voicenotes.com"
REDIRECT_PORT = 18765
REDIRECT_URI = "http://localhost:%d/callback" % REDIRECT_PORT


UA = "JarvisWatchBridge/0.2 (Voicenotes MCP OAuth)"


def _get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover():
    """按 MCP 规范(RFC 9728)发现授权服务器; 优先 /mcp 路径专属元数据."""
    servers = [ORIGIN]
    for path in ("/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource"):
        try:
            pr = _get_json(ORIGIN + path)
            found = pr.get("authorization_servers")
            if found:
                servers = found
                break
        except Exception:
            continue
    meta = {}
    for server in servers:
        try:
            meta = _get_json(server.rstrip("/") + "/.well-known/oauth-authorization-server")
            if meta.get("authorization_endpoint"):
                break
        except Exception:
            continue
    if not meta.get("authorization_endpoint"):
        raise RuntimeError("无法发现 Voicenotes 授权端点, 请稍后重试或查阅官方文档")
    return meta


def register(meta):
    """动态客户端注册; 注册端点缺失时按公共客户端无 secret 处理."""
    reg_url = meta.get("registration_endpoint")
    if not reg_url:
        return None
    body = json.dumps({
        "client_name": "Jarvis Watch Bridge",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code"],
        "token_endpoint_auth_method": "none",
        "response_types": ["code"],
    }).encode()
    req = urllib.request.Request(reg_url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _Waiter(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _Waiter.code = (params.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>✅ Jarvis 已获得 Voicenotes 授权, 可以关闭此页面</h2>".encode())

    def log_message(self, *args):
        pass


def wait_for_code(timeout=1800):
    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _Waiter)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    server.server_close()
    return _Waiter.code


def save_state(meta, client_id, verifier):
    """保存PKCE状态, 供手动兑换模式使用(手机等设备完成授权后回填)."""
    state = {"client_id": client_id, "verifier": verifier,
             "token_endpoint": meta["token_endpoint"]}
    with open(os.path.join(BASE, ".run", "oauth_state.json"), "w") as f:
        json.dump(state, f)


def manual_exchange(callback_url):
    """从浏览器地址栏复制的回调URL中提取code, 用保存的verifier换令牌."""
    state_path = os.path.join(BASE, ".run", "oauth_state.json")
    if not os.path.exists(state_path):
        print("[oauth] 没有待兑换的授权状态, 请先运行一次授权流程")
        sys.exit(1)
    with open(state_path) as f:
        state = json.load(f)
    parsed = urllib.parse.urlparse(callback_url.strip())
    code = (urllib.parse.parse_qs(parsed.query).get("code") or [None])[0]
    if not code:
        print("[oauth] 回调URL里没有找到 code 参数")
        sys.exit(1)
    tokens = exchange({"token_endpoint": state["token_endpoint"]},
                      state["client_id"], code, state["verifier"])
    token = tokens.get("access_token")
    if not token:
        print("[oauth] 兑换失败, 返回字段: " + ",".join(tokens.keys()))
        sys.exit(1)
    save_token(token)
    os.remove(state_path)
    print("[oauth] ✅ 手动兑换完成, 令牌已写入 .env, 重启Jarvis生效")


def exchange(meta, client_id, code, verifier):
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id or "",
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(meta["token_endpoint"], data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": UA},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_token(token):
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    entry = "VOICENOTES_ACCESS_TOKEN=%s\n" % token
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("VOICENOTES_ACCESS_TOKEN="):
            lines[i] = entry
            replaced = True
            break
    if not replaced:
        lines.append(entry)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    global REDIRECT_PORT, REDIRECT_URI
    if len(sys.argv) >= 3 and sys.argv[1] == "--exchange":
        manual_exchange(sys.argv[2])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--public-redirect":
        # 公网HTTPS回调: 绕开Cloudflare WAF对http://localhost的重定向拦截
        base = sys.argv[2].rstrip("/")
        REDIRECT_PORT = 18766
        REDIRECT_URI = base + "/callback"
        print("[oauth] 公网回调模式: " + REDIRECT_URI)
    print("[oauth] 正在发现 Voicenotes 授权端点...")
    meta = discover()
    print("[oauth] 发现成功: authorization_endpoint 已获取")
    client = register(meta)
    client_id = (client or {}).get("client_id")
    print("[oauth] 动态注册: " + ("完成" if client_id else "端点未提供, 使用公共客户端"))

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    scope = (meta.get("scopes_supported") or ["mcp:use"])[0]
    params = {
        "response_type": "code",
        "client_id": client_id or "",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": scope,
    }
    if not client_id:
        params.pop("client_id")
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    os.makedirs(os.path.join(BASE, ".run"), exist_ok=True)
    save_state(meta, client_id, verifier)
    print("[oauth] 即将打开浏览器, 请登录 Voicenotes 并点击允许...")
    print("[oauth] 若浏览器未自动打开, 手动访问:\n" + url)
    webbrowser.open(url)

    code = wait_for_code()
    if not code:
        print("[oauth] 未收到授权码(超时或被拒绝), 未做任何修改")
        sys.exit(1)
    tokens = exchange(meta, client_id, code, verifier)
    token = tokens.get("access_token")
    if not token:
        print("[oauth] 未取得 access_token, 返回字段: " + ",".join(tokens.keys()))
        sys.exit(1)
    save_token(token)
    print("[oauth] ✅ 授权完成, 令牌已写入 .env (VOICENOTES_ACCESS_TOKEN), 未在终端显示")
    print("[oauth] 重启 Jarvis 生效: ./start_jarvis.sh restart")


if __name__ == "__main__":
    main()
