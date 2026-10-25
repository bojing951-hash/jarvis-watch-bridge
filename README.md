# Jarvis Watch Bridge v0.1

Apple Watch / iPhone 快捷指令 → 本机 AI 问答桥接服务。

```text
Apple Watch 快捷指令(听写) → iPhone/互联网 → Cloudflare Quick Tunnel
  → 本服务(127.0.0.1:8765, X-Jarvis-Key认证) → GLM通用API(demo/glm)
  → 返回 {"reply":"..."} → 快捷指令朗读 → AirPods
```

个人记忆系统不在本项目范围内（由 Apple Watch + Voicenotes 承担）。

## 文件结构

| 文件 | 作用 |
|---|---|
| `server.py` | HTTP 服务：`GET /health`、`POST /ask`（X-Jarvis-Key 认证，仅监听 127.0.0.1） |
| `glm_provider.py` | GLM 通用 API 调用（OpenAI 兼容接口，直连不走系统代理） |
| `demo_provider.py` | 无密钥时的演示应答（不编造知识性答案） |
| `setup.py` | 初始化：生成 `.env` 与随机 `JARVIS_TOKEN`（不覆盖已有配置） |
| `start_jarvis.sh` | 一键 `start / stop / status / restart`（含防重复启动、健康检查、公网验证） |
| `tests/` | 自动化测试：`./run_tests.sh`（16 项；配了 GLM 密钥后含真实模型调用） |
| `IPHONE_SHORTCUT_SETUP.md` | iPhone/Watch 快捷指令最终配置说明 |

## 日常使用

```bash
cd /Users/cast/AI内容/贾维斯/JarvisWatchBridge
./start_jarvis.sh          # 启动并显示公网地址
./start_jarvis.sh status   # 查看当前状态与公网地址(重启后地址会变)
./start_jarvis.sh stop     # 停止
./run_tests.sh             # 跑全部自动化测试
```

## 启用真实 GLM 回答（默认 demo 模式）

编辑 `.env`，把通用 API 密钥填入（**不要**使用 GLM Coding Plan 编程接口的密钥用途）：

```
GLM_API_KEY=你的通用API密钥
```

然后 `./start_jarvis.sh restart`。重启后隧道地址会变，记得按
`IPHONE_SHORTCUT_SETUP.md` 更新快捷指令里的 URL。

## 安全设计

- 服务只监听 `127.0.0.1`，公网访问全部经 Cloudflare Tunnel，无路由器端口映射
- `/ask` 必须携带 `X-Jarvis-Key`，与 `.env` 的 `JARVIS_TOKEN` 常数时间比对；错误令牌一律 401
- 无任何远程命令执行能力；令牌与密钥不写入代码、不写入日志
- 本机 Clash 系统代理坑已处理：服务端 GLM 调用与本地测试均强制直连

## 已知限制

- Quick Tunnel 地址每次重启变化，是开发测试方案，非生产部署
- GLM 推理模型响应可能超过 10 秒（系统提示词已要求 100 字内简洁回答）
- 回答无联网搜索能力，系统提示词已禁止声称"已查询最新信息"
- 电脑休眠/关机时服务不可达
