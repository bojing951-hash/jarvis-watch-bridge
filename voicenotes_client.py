"""Voicenotes 官方 MCP 客户端 (Streamable HTTP, JSON-RPC 2.0).

端点: https://api.voicenotes.com/mcp  认证: Bearer 访问令牌 (由 voicenotes_oauth.py 获取)
官方工具: search_notes / list_notes / get_transcript / create_text_note
(确切参数名以运行时 tools/list 返回的 schema 为准, 本客户端在连接时自适应)

可靠性: 任何网络/协议错误统一抛 VoicenotesUnavailable, 由上层降级处理,
绝不编造记录内容。
"""
import json
import socket
import urllib.error
import urllib.request

MCP_PROTOCOL_VERSION = "2025-03-26"
_TOOL_HINTS = {
    "search": ["search_notes", "search", "search_note"],
    "list": ["list_notes", "list", "list_note"],
    "create": ["create_text_note", "create_note", "create_text"],
    "transcript": ["get_transcript", "get_transcription", "transcript"],
}
# 日期参数在各家 schema 中的常见命名, 按命中顺序取
_FROM_KEYS = ["date_from", "from", "start", "start_date", "startDate", "after", "begin"]
_TO_KEYS = ["date_to", "to", "end", "end_date", "endDate", "before"]
_RANGE_KEYS = ["date_range", "daterange", "range"]  # 合并式 [start, end] 数组参数
_QUERY_KEYS = ["query", "q", "search", "text", "keywords"]
_CONTENT_KEYS = ["transcript", "content", "text", "note", "body", "message"]
_TAG_KEYS = ["tags", "tag"]


class VoicenotesUnavailable(Exception):
    """Voicenotes 暂不可用或未授权 — 调用方需如实告知用户, 不得编造记忆."""


def _day_bounds_utc(date_iso, tz=None):
    """把本地日期转成 UTC 闭开区间 [当天00:00, 次日00:00) 的 ISO 串.

    tz 缺省用系统本地时区(避免把 UTC 日期当本地日期导致边界偏移);
    传入显式 tz 供单元测试.
    """
    from datetime import date, datetime, timedelta, timezone
    d = date.fromisoformat(date_iso)
    start_local = datetime(d.year, d.month, d.day)
    if tz is None:
        start_utc = start_local.astimezone(timezone.utc)  # naive→本地时区→UTC
    else:
        start_utc = start_local.replace(tzinfo=tz).astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start_utc.strftime(fmt), end_utc.strftime(fmt)


def _pick(schema_props, candidates):
    for name in candidates:
        if name in schema_props:
            return name
    return None


class VoicenotesClient:
    def __init__(self, endpoint, token, timeout=20):
        if not token:
            raise VoicenotesUnavailable("未配置 Voicenotes 访问令牌")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._next_id = 1
        self._tools = {}  # name -> schema properties

    # ---- MCP 基础协议 ----
    def _post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer " + self.token,
                "User-Agent": "JarvisWatchBridge/0.2 (Voicenotes MCP client)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise VoicenotesUnavailable("Voicenotes 授权无效或已过期, 需要重新授权")
            raise VoicenotesUnavailable("Voicenotes 服务返回 HTTP %d" % e.code)
        except (socket.timeout, urllib.error.URLError):
            raise VoicenotesUnavailable("无法连接 Voicenotes 服务(网络异常)")
        except ConnectionError:
            raise VoicenotesUnavailable("无法连接 Voicenotes 服务(连接中断)")
        return self._parse_response(raw)

    @staticmethod
    def _parse_response(raw):
        # Streamable HTTP: 可能是纯 JSON, 也可能是 text/event-stream
        if not raw:
            raise VoicenotesUnavailable("Voicenotes 返回了空响应")
        text = raw.strip()
        if text.startswith("{"):
            return json.loads(text)
        # SSE: 取最后一条 data: 行
        last = None
        for line in text.splitlines():
            if line.startswith("data:"):
                last = line[5:].strip()
        if not last:
            raise VoicenotesUnavailable("Voicenotes 响应格式无法解析")
        return json.loads(last)

    def _rpc(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        result = self._post(payload)
        if isinstance(result, dict) and result.get("error"):
            raise VoicenotesUnavailable("Voicenotes 接口错误: %s"
                                        % result["error"].get("message", "unknown"))
        return result.get("result", result)

    def connect(self):
        """initialize + tools/list, 记录各工具的参数 schema."""
        self._rpc("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jarvis-watch-bridge", "version": "0.2"},
        })
        try:  # initialized 通知, 部分服务要求
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except VoicenotesUnavailable:
            pass
        listing = self._rpc("tools/list", {})
        self._tools = {}
        for tool in (listing or {}).get("tools", []):
            props = ((tool.get("inputSchema") or {}).get("properties")) or {}
            self._tools[tool.get("name", "")] = props
        return self._tools

    def _tool_name(self, kind):
        if not self._tools:
            self.connect()
        for hint in _TOOL_HINTS[kind]:
            if hint in self._tools:
                return hint
        for name in self._tools:
            if kind in name.lower():
                return name
        raise VoicenotesUnavailable("Voicenotes 未提供 %s 工具" % kind)

    def _call(self, kind, arguments):
        name = self._tool_name(kind)
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return self._text_of(result)

    @staticmethod
    def _text_of(result):
        parts = []
        for block in (result or {}).get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        text = "\n".join(parts).strip()
        if (result or {}).get("isError"):
            # 工具层失败必须如实上抛, 严禁包装成成功
            raise VoicenotesUnavailable("Voicenotes 工具执行失败: " + text[:120])
        return text

    # ---- 面向 Jarvis 的语义接口 ----
    def search(self, query):
        """语义检索个人记录, 返回服务端文本结果(可能为空字符串=没找到)."""
        name = self._tool_name("search")
        key = _pick(self._tools.get(name, {}), _QUERY_KEYS) or "query"
        return self._call("search", {key: query})

    def list_notes(self, date_from=None, date_to=None):
        """按日期范围列出记录.

        入参是用户本地日期(ISO); schema 为合并式 date_range 数组时,
        转成 UTC 闭开区间并按本地时区取整天边界, 避免跨时区漏记.
        """
        name = self._tool_name("list")
        props = self._tools.get(name, {})
        args = {}
        rk = _pick(props, _RANGE_KEYS)
        if date_from and rk:
            start, _ = _day_bounds_utc(date_from)
            # 结束边界: date_to 当天的次日零点(闭开区间覆盖整天)
            _, end = _day_bounds_utc(date_to or date_from)
            args[rk] = [start, end]
            return self._call("list", args)
        fk = _pick(props, _FROM_KEYS)
        tk = _pick(props, _TO_KEYS)
        if date_from and fk:
            args[fk] = date_from
        if date_to and tk:
            args[tk] = date_to
        return self._call("list", args)

    def create_note(self, text, tags=None):
        name = self._tool_name("create")
        props = self._tools.get(name, {})
        ck = _pick(props, _CONTENT_KEYS) or "content"
        args = {ck: text}
        tk = _pick(props, _TAG_KEYS)
        if tags and tk:
            args[tk] = tags
        return self._call("create", args)
