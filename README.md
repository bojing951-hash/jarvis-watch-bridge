# Jarvis Watch Bridge v0.3

Apple Watch / iPhone 快捷指令 → 本机 AI 问答 + 联网搜索 + 个人记忆桥接服务。

```text
Apple Watch 快捷指令(听写) → iPhone/互联网 → Cloudflare Quick Tunnel
  → 本服务(127.0.0.1:8765, X-Jarvis-Key认证)
       ├─ 实时问题 → 智谱联网搜索(7天内新鲜结果) → GLM 组织口语回答
       ├─ general_question → GLM通用API(demo/glm)
       └─ memory_search / memory_create / memory_summary → Voicenotes MCP
  → /ask-text 返回纯文本(或 /ask 返回JSON) → 快捷指令朗读 → AirPods
```

## v0.3 新增

- **POST /ask-text 纯文本语音接口**：响应体就是可朗读的回答（`text/plain`），
  手表端彻底摆脱 JSON/字典解析（根治"无法将富文本转换为字典"报错）
- **真实联网搜索**（`web_search.py`）：智谱独立搜索 API（search_pro），
  时效路由只在需要时联网（实时行情/新闻/明确搜索指令），结果按发布日期过滤
  （7 天内才算"最新"），旧数据不冒充最新；金融方向规则内置（USD/CNH 上升=人民币贬值），
  搜索来源本地留档（`.run/search_audit.jsonl`）备核查
- **诊断体系**：每请求 `X-Jarvis-Request-Id` + 日志分段耗时（模型/搜索/MCP）+
  回答字数；日志仍不记录问题正文
- **指标不虚报**：区分 HTTP 成功与业务成功（回答成功/降级/失败分开统计）
- **记忆安全**：重复记录指令自动查重不重复写入；写入结果未知时先核实再答复；
  日期总结按本地时区取整天边界（UTC 闭开区间）
- **路由修复**：设备故障问句（"我的AirPods连不上"）不再误入个人记忆检索
- **健壮性**：请求体上限 64KB（413）、HTTP/1.1、GLM 截断检测、`max_tokens` 600

## v0.2 功能（继承）

- 意图路由：普通问答 / 记忆查询 / 创建记录 / 日期总结；创建指令不明确先确认
- Voicenotes 官方 MCP：`search_notes` / `list_notes` / `create_text_note`，schema 自适应
- OAuth 一键授权（v0.3 增加 state 防 CSRF；`.env` 权限 600）
- 隐私：日志不记问题正文；错误信息不含内部地址/密钥
- glm_provider：空 `content` 一律重试/报错，绝不朗读 `reasoning_content`

## 启用个人记忆（一次）

```bash
cd /Users/cast/AI内容/贾维斯/JarvisWatchBridge
python3 voicenotes_oauth.py   # 浏览器登录 Voicenotes 并允许
./start_jarvis.sh restart     # 重启生效 (隧道地址会变, 更新快捷指令)
```

## 个人记忆历史

v0.1：初始问答桥（`/health` `/ask`、GLM demo/glm 双模式、Quick Tunnel、一键脚本）。
记忆系统此前由 Apple Watch + Voicenotes App 承担，v0.2 起由本服务经官方 MCP 直连。

## 文件结构

| 文件 | 作用 |
|---|---|
| `server.py` | HTTP 服务：`GET /health`、`GET /metrics`、`POST /ask`（JSON）、`POST /ask-text`（纯文本） |
| `router.py` | 意图路由（general_question / memory_search / memory_create / memory_summary） |
| `glm_provider.py` | GLM 通用 API 调用（OpenAI 兼容接口，直连不走系统代理） |
| `web_search.py` | 智谱联网搜索（时效路由 / 日期过滤 / 查询优化，独立可替换） |
| `voicenotes_client.py` | Voicenotes 官方 MCP 客户端（检索/日期列表/创建笔记，本地时区边界） |
| `voicenotes_oauth.py` | Voicenotes 浏览器 OAuth 一键授权（state 校验，令牌写入本地 .env） |
| `demo_provider.py` | 无密钥时的演示应答（不编造知识性答案） |
| `setup.py` | 初始化：生成 `.env` 与随机 `JARVIS_TOKEN`（不覆盖已有配置） |
| `start_jarvis.sh` | 一键 `start / stop / status / restart`（含防重复启动、健康检查、公网验证） |
| `tests/` | 自动化测试：`./run_tests.sh`（66 项；配了 GLM 密钥后含真实模型调用） |
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
