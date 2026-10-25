#!/usr/bin/env python3
"""Jarvis Watch Bridge 初始化: 生成 .env 与强随机 JARVIS_TOKEN.

用法: python3 setup.py
- .env 不存在时, 基于 .env.example 创建, 并生成 48 位十六进制随机令牌
- .env 已存在时, 不覆盖任何已有配置 (安全默认)
"""
import os
import secrets

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")
EXAMPLE_PATH = os.path.join(BASE, ".env.example")


def main():
    if os.path.exists(ENV_PATH):
        print("[setup] .env 已存在, 保持不变 (如需重置请手动删除后重新运行)")
        return
    token = secrets.token_hex(24)
    lines = []
    example = EXAMPLE_PATH if os.path.exists(EXAMPLE_PATH) else None
    if example:
        with open(example, encoding="utf-8") as f:
            for line in f:
                if line.startswith("JARVIS_TOKEN="):
                    lines.append("JARVIS_TOKEN=" + token + "\n")
                else:
                    lines.append(line)
    else:
        lines = [
            "JARVIS_TOKEN=" + token + "\n",
            "PROVIDER=auto\n",
            "GLM_API_KEY=\n",
            "GLM_MODEL=glm-5.3-flash\n",
            "GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4\n",
            "GLM_TIMEOUT=45\n",
            "HOST=127.0.0.1\n",
            "PORT=8765\n",
        ]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("[setup] 已创建 .env 并生成随机 JARVIS_TOKEN")
    print("[setup] 查看令牌: grep JARVIS_TOKEN .env")


if __name__ == "__main__":
    main()
