"""GLM 通用 API Provider (OpenAI 兼容 chat/completions 接口).

角色定义: 这是 Jarvis 日常问答的推理服务 (角色B).
明确不使用 GLM Coding Plan 编程接口 — 那是 Claude Code (角色A) 专用额度.

超时、鉴权、限流等错误统一转换为可口播的中文 ProviderError,
由 server.py 决定如何呈现给 Apple Watch.
"""
import json
import socket
import urllib.error
import urllib.request

# 直连 opener: 绕过系统/环境代理 (本机 Clash 代理会拦截国内 API 与本地回环请求)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SYSTEM_PROMPT = (
    "你是随身语音助手Jarvis，通过AirPods向用户朗读回答。规则："
    "1.用中文口语回答；2.结论先行；3.总长度控制在100字以内；"
    "4.不要使用Markdown、列表、代码块或表情符号；"
    "5.不确定时明确说明不确定；6.你没有联网搜索能力，不得声称已查询或验证最新信息。"
)


class ProviderError(Exception):
    """带用户可读中文信息的模型调用失败."""


def answer(question, api_key, model, base_url, timeout=45):
    """调用 GLM chat/completions, 返回纯文本回答. 失败抛 ProviderError."""
    if not api_key:
        raise ProviderError("未配置 GLM_API_KEY")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
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
        raise ProviderError("无法连接GLM接口: %s" % reason)
    except json.JSONDecodeError:
        raise ProviderError("GLM返回了无法解析的内容")

    try:
        message = data["choices"][0]["message"]
        reply = (message.get("content") or "").strip()
        if not reply:
            # 部分推理模型可能把结论放在 reasoning_content, 取末段兜底
            rc = (message.get("reasoning_content") or "").strip()
            reply = rc.splitlines()[-1].strip() if rc else ""
    except (KeyError, IndexError, AttributeError):
        raise ProviderError("GLM返回格式异常，无法提取回答")
    if not reply:
        raise ProviderError("GLM返回了空回答")
    return reply
