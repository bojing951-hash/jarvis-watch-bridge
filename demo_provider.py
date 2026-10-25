"""Demo Provider: 未配置 GLM 密钥时的本地应答.

设计原则: 不编造知识性答案, 只确认链路与说明配置方法, 文本可直接语音朗读.
"""

REPLY = (
    "（演示模式）连接正常，我已收到你的问题。但当前电脑没有配置模型密钥，"
    "我无法回答知识性问题。请在电脑上编辑 .env 文件，填写 GLM_API_KEY 后重启 Jarvis。"
)


def answer(question: str) -> str:
    """返回确定性的演示回复; 参数仅为保持与 glm_provider 相同的接口签名."""
    return REPLY
