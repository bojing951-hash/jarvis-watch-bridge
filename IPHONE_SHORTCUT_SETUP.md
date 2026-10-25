# iPhone / Apple Watch 快捷指令接入 Jarvis — 最终配置

> 以下接口地址与请求格式均已在电脑端真实测试通过（2026-10-25）。
> 你在 Apple Watch 上已验证成功的「听写文本」和「朗读文本」保持原样，不要改动。

## 当前有效地址

```
https://enters-post-patients-dale.trycloudflare.com/ask
```

⚠️ **重要：Quick Tunnel 是临时地址，电脑端每次重启 Jarvis 后地址会变化。**
变化后在电脑上运行以下命令获取新地址，并更新快捷指令里的 URL：

```bash
cd /Users/cast/AI内容/贾维斯/JarvisWatchBridge && ./start_jarvis.sh status
```

## 获取访问令牌（在电脑上执行，不要截图外传）

```bash
grep JARVIS_TOKEN /Users/cast/AI内容/贾维斯/JarvisWatchBridge/.env
```

显示的 `=` 后面那一串就是令牌，复制它。

---

## 快捷指令五个动作（在你现有快捷指令上修改）

### 动作一：听写文本（保留现状）
- 语言：中文（普通话）；结束方式：停顿后结束
- 此动作你已实测成功，不需要任何修改

### 动作二：URL
```
https://enters-post-patients-dale.trycloudflare.com/ask
```
（用「获取访问令牌」一节的方法确认地址为最新）

### 动作三：获取 URL 内容
- 方法：**POST**
- 请求头：
  - `X-Jarvis-Key` → 粘贴你从电脑上复制的令牌
- 请求体类型：**JSON**
  - `question` → 选择**动作一听写文本的输出变量**（不要手打"听写文本"四个字）
  - `session` → 普通文本 `watch`

### 动作四：获取词典值
- 键：`reply`
- 输入来源：动作三「获取 URL 内容」的返回值

### 动作五：朗读文本
- 朗读内容：选择动作四取到的 `reply`
- 语言中文；语速可按喜好调整

---

## 服务器返回格式（供排错参考）

成功（HTTP 200）：

```json
{"reply": "可直接朗读的中文回答", "provider": "demo 或 glm"}
```

失败：HTTP 401 = 令牌错误；HTTP 400 = 请求体格式问题。

## 在 Apple Watch 上运行

- 快捷指令会自动同步到 Watch 上的「快捷指令」App；也可用 Siri 说快捷指令的名字触发
- 第一次运行若请求转圈很久：检查电脑端 `./start_jarvis.sh status` 是否显示「公网地址」正常
- 回答需要电脑在线：电脑休眠或 Jarvis 停止时手表会请求失败

## 必须实机验收的项目（电脑无法替你验证）

1. **AirPods 音频路由**：watchOS 不保证自动选择 AirPods 麦克风/扬声器——请在手表上实测听写收音和朗读播放各走哪个设备；不对则在手表设置中手动选音频输出
2. **锁屏/抬腕触发**：手腕抬起直接问 vs 需要点亮屏幕点一下快捷指令，以实测为准
3. 蜂窝网络/4G 下的可达性（Quick Tunnel 在部分运营商网络可能不稳定，换个网络再试即可判断）
