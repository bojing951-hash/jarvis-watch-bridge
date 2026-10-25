"""GLM 通用 API Provider (OpenAI 兼容 chat/completions 接口).

角色定义: 这是 Jarvis 日常问答的推理服务 (角色B).
明确不使用 GLM Coding Plan 编程接口 — 那是 Claude Code (角色A) 专用额度.

超时、鉴权、限流等错误统一转换为可口播的中文 ProviderError,
由 server.py 决定如何呈现给 Apple Watch.
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# 直连 opener: 绕过系统/环境代理 (本机 Clash 代理会拦截国内 API 与本地回环请求)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 最近一次调用的元数据 (线程局部): finish_reason / token 用量 / 重试次数.
# 只用于日志诊断 (server 汇总进请求日志行), 不含问题与回答内容.
_tls = threading.local()


def last_meta():
    """返回最近一次 answer() 的元数据 dict (本线程), 无则空 dict."""
    return getattr(_tls, "meta", None) or {}

SYSTEM_PROMPT = (
    "你是随身语音助手Jarvis，通过AirPods向用户朗读回答。规则："
    "1.用中文口语回答；2.结论先行；3.总长度控制在100字以内；"
    "4.不要使用Markdown、列表、代码块或表情符号；"
    "5.不确定时明确说明不确定；6.你没有联网搜索能力，不得声称已查询或验证最新信息。"
)


class ProviderError(Exception):
    """带用户可读中文信息的模型调用失败."""


def answer(question, api_key, model, base_url, timeout=45, system=None, retries=1):
    """调用 GLM, 返回纯文本回答. 失败抛 ProviderError.

    推理模型偶发只输出 reasoning_content 而 content 为空,
    对该情况最多重试 retries 次, 其余错误直接抛出.
    """
    last = None
    attempts = 0
    for _ in range(retries + 1):
        attempts += 1
        _tls.meta = {"attempts": attempts}
        try:
            return _answer_once(question, api_key, model, base_url, timeout, system)
        except ProviderError as e:
            if "空回答" not in str(e):
                raise
            last = e
            time.sleep(1)
    raise last


def _answer_once(question, api_key, model, base_url, timeout=45, system=None):
    """单次调用 GLM chat/completions.

    v0.2: system 可覆盖 (记忆问答用更严格的不编造提示);
    不再读取 reasoning_content — 推理过程不是给用户朗读的答案.
    """
    if not api_key:
        raise ProviderError("未配置 GLM_API_KEY")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.3,
        # 推理模型的 completion_tokens 含思考段(实测127字回答耗594),
        # 留足余量防止 finish=length 把正文截掉
        "max_tokens": 800,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    try:
        with DIRECT_OPENER.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ProviderError("GLM密钥无效或未授权(401)，请检查GLM_API_KEY")
        if e.code == 429:
            raise ProviderError("GLM调用被限流或额度不足(429)")
        if e.code == 403:
            raise ProviderError("GLM拒绝访问(403)，可能无该模型权限")
        raise ProviderError("GLM接口返回HTTP %d" % e.code)
    except socket.timeout:
        raise ProviderError("GLM响应超时，可稍后重试")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout):
            raise ProviderError("GLM响应超时，可稍后重试")
        # 错误信息只描述类别, 不回显内部地址等配置
        raise ProviderError("无法连接GLM服务（网络异常）")
    except json.JSONDecodeError:
        raise ProviderError("GLM返回了无法解析的内容")

    try:
        choice = data["choices"][0]
        message = choice["message"]
        reply = (message.get("content") or "").strip()
    except (KeyError, IndexError, AttributeError):
        raise ProviderError("GLM返回格式异常，无法提取回答")
    if not reply:
        # v0.2: 空 content 一律视为失败, 不朗读 reasoning_content
        raise ProviderError("GLM返回了空回答")
    usage = data.get("usage") or {}
    _tls.meta = {
        "finish": choice.get("finish_reason") or "?",
        "ctok": usage.get("completion_tokens"),
        "ptok": usage.get("prompt_tokens"),
        "attempts": (getattr(_tls, "meta", None) or {}).get("attempts", 1),
    }
    if choice.get("finish_reason") == "length":
        # 达到 token 上限被截断: 保留内容但记录, 供排查"朗读不完整"
        sys.stderr.write("[glm] 回答被 max_tokens 截断(%d字)\n" % len(reply))
    return reply
