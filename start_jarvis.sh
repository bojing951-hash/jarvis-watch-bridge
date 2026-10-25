#!/bin/bash
# Jarvis Watch Bridge 一键启动/停止/状态
# 用法: ./start_jarvis.sh [start|stop|status|restart]   (默认 start)
#
# 功能: 检查环境 → 启动本机服务(仅127.0.0.1:8765) → 启动 Cloudflare Quick Tunnel
#       → 健康检查 → 输出公网地址。支持防重复启动与干净停止。

set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$BASE/.run"
SERVER_PID="$RUN_DIR/server.pid"
TUNNEL_PID="$RUN_DIR/tunnel.pid"
SERVER_LOG="$RUN_DIR/server.log"
TUNNEL_LOG="$RUN_DIR/tunnel.log"
URL_FILE="$RUN_DIR/tunnel.url"
LOCAL="http://127.0.0.1:8765"
CLOUDFLARED="/Users/cast/.local/bin/cloudflared"
[ -x "$CLOUDFLARED" ] || CLOUDFLARED="$(command -v cloudflared || true)"

mkdir -p "$RUN_DIR"

# 本机回环请求必须绕过系统代理(Clash)
local_curl() { curl -s --noproxy '*' --max-time 5 "$@"; }

pid_alive() {  # $1=pidfile $2=关键词; 仅当进程存在且命令行匹配关键词才视为我们的进程
    local pid kw
    [ -f "$1" ] || return 1
    pid="$(cat "$1" 2>/dev/null)"
    kw="${2:-jarvis}"
    [ -n "$pid" ] || return 1
    ps -p "$pid" -o command= 2>/dev/null | grep -q "$kw"
}

server_healthy() { local_curl -o /dev/null -w '%{http_code}' "$LOCAL/health" 2>/dev/null | grep -q 200; }

tunnel_url() {
    [ -f "$URL_FILE" ] && cat "$URL_FILE" && return 0
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1
}

start_server() {
    if server_healthy; then
        echo "[jarvis] 服务已在运行 ($LOCAL)"
        return 0
    fi
    if pid_alive "$SERVER_PID" "server.py"; then
        echo "[jarvis] 发现残留服务进程, 先停止..."
        kill "$(cat "$SERVER_PID")" 2>/dev/null; sleep 1
    fi
    if ! pid_alive "$SERVER_PID" "server.py"; then rm -f "$SERVER_PID"; fi
    if lsof -nP -i :8765 -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[jarvis] 错误: 8765端口被其他程序占用, 请先处理 (lsof -i :8765)"; exit 1
    fi
    [ -f "$BASE/.env" ] || python3 "$BASE/setup.py"
    echo "[jarvis] 启动服务..."
    nohup python3 "$BASE/server.py" >> "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PID"
    for i in $(seq 1 10); do
        server_healthy && { echo "[jarvis] 服务就绪 ($LOCAL)"; return 0; }
        sleep 1
    done
    echo "[jarvis] 服务启动失败, 最近日志:"; tail -5 "$SERVER_LOG"; exit 1
}

start_tunnel() {
    if pid_alive "$TUNNEL_PID" "cloudflared" && [ -n "$(tunnel_url)" ]; then
        echo "[jarvis] 隧道已在运行: $(tunnel_url)"
        return 0
    fi
    if [ -z "$CLOUDFLARED" ] || [ ! -x "$CLOUDFLARED" ]; then
        echo "[jarvis] 错误: 未找到 cloudflared"; exit 1
    fi
    echo "[jarvis] 启动 Cloudflare Quick Tunnel..."
    rm -f "$URL_FILE"
    : > "$TUNNEL_LOG"   # 清空旧日志, 避免读到上一次隧道的过期地址
    nohup env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
        "$CLOUDFLARED" tunnel --url "$LOCAL" --no-autoupdate \
        >> "$TUNNEL_LOG" 2>&1 &
    echo $! > "$TUNNEL_PID"
    local url=""
    for i in $(seq 1 30); do
        url="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1)"
        [ -n "$url" ] && break
        sleep 1
    done
    if [ -z "$url" ]; then
        echo "[jarvis] 隧道启动失败, 最近日志:"; tail -5 "$TUNNEL_LOG"; exit 1
    fi
    echo "$url" > "$URL_FILE"
    echo "[jarvis] 隧道就绪: $url"
}

cmd_start() {
    command -v python3 >/dev/null || { echo "[jarvis] 错误: 缺少 python3"; exit 1; }
    start_server
    start_tunnel
    local url; url="$(tunnel_url)"
    echo
    echo "================ Jarvis 已就绪 ================"
    echo "  本机服务 : $LOCAL  (仅监听127.0.0.1)"
    echo "  公网地址 : $url/ask"
    echo "  健康检查 : $url/health"
    local prov; prov="$(local_curl "$LOCAL/health" | sed -n 's/.*"provider": *"\([^"]*\)".*/\1/p')"
    echo "  推理服务 : ${prov:-未知}  (demo=未配GLM密钥)"
    local pub_ok="异常 (请运行 ./start_jarvis.sh status 复查; 仍异常则看 .run/tunnel.log)"
    for i in $(seq 1 12); do
        if [ "$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$url/health" 2>/dev/null)" = "200" ]; then
            pub_ok="正常"; break
        fi
        sleep 3   # 隧道URL打印后, 边缘连接注册完成前不可路由, 最多等约84秒
    done
    echo "  公网连通 : $pub_ok"
    echo "  访问令牌 : 查看 .env 中 JARVIS_TOKEN (不要泄露)"
    echo "  停止     : ./start_jarvis.sh stop"
    echo "================================================"
    echo "  注意: Quick Tunnel 地址在每次重启后会变化,"
    echo "  变化后运行 ./start_jarvis.sh status 查看新地址。"
}

cmd_stop() {
    local stopped=0
    if pid_alive "$TUNNEL_PID" "cloudflared"; then
        kill "$(cat "$TUNNEL_PID")" 2>/dev/null && stopped=1 && echo "[jarvis] 已停止隧道"
    fi
    rm -f "$TUNNEL_PID" "$URL_FILE"
    if pid_alive "$SERVER_PID" "server.py"; then
        kill "$(cat "$SERVER_PID")" 2>/dev/null && stopped=1 && echo "[jarvis] 已停止服务"
    fi
    rm -f "$SERVER_PID"
    if [ $stopped -eq 0 ]; then
        echo "[jarvis] 没有在运行的进程"
    fi
    return 0
}

cmd_status() {
    echo "================ Jarvis 状态 ================"
    if server_healthy; then
        echo "  本机服务 : 运行中 ($LOCAL)  $(local_curl "$LOCAL/health")"
    else
        echo "  本机服务 : 未运行"
    fi
    if pid_alive "$TUNNEL_PID" "cloudflared"; then
        echo "  隧道进程 : 运行中 (pid $(cat "$TUNNEL_PID"))"
        echo "  公网地址 : $(tunnel_url)"
    else
        echo "  隧道进程 : 未运行"
    fi
    echo "============================================="
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; sleep 1; cmd_start ;;
    *) echo "用法: $0 [start|stop|status|restart]"; exit 1 ;;
esac
