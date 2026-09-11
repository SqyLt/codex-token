#!/usr/bin/env bash
# 伴随脚本：Codex 打开时启动看板服务与用量窗口，Codex 退出时自动关闭窗口。
# 由 ~/.config/autostart/codex-usage-companion.desktop 在登录时拉起。
set -u

DIR="$HOME/.local/share/codex-usage"
PORT="${CODEX_USAGE_PORT:-8788}"
APP_MATCH="/usr/lib/chatgpt/ChatGPT"
LOG="$DIR/companion.log"
WINDOW_PID=""
USER_CLOSED=0
LAST_MAIN=""

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

server_ok() { curl -s -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }

ensure_server() {
  server_ok && return 0
  log "启动看板服务"
  ( cd "$DIR" && setsid nohup node dashboard-server.mjs >>"$LOG" 2>&1 </dev/null & )
  for _ in $(seq 1 12); do
    sleep 0.3
    server_ok && return 0
  done
  return 1
}

# 只认 Codex 主进程：Electron 的 --type= 辅助进程会晚于主进程退出，
# 用它判断“Codex 是否在运行”会漏掉重启，导致窗口再也不自动打开。
main_pid() {
  local p cmd
  for p in $(pgrep -x ChatGPT 2>/dev/null); do
    cmd=$(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null) || continue
    case "$cmd" in
      *--type=*) continue ;;
    esac
    printf '%s' "$p"
    return 0
  done
  printf ''
}

app_running() {
  [ -n "$(main_pid)" ] && return 0
  pgrep -f "^${APP_MATCH}\$" >/dev/null 2>&1
}

# 规避 Codex 沙箱在“只读根内部的可写根”上建挂载点失败的问题：
# 每个任务的 visualization 目录（~/.codex/visualizations/<日期>/<任务ID>）会被注入成可写根，
# 而 ~/.codex 本身是只读根，bwrap 建不出 .git/.agents/.codex 三个挂载点就会报
# “Read-only file system”，导致该任务里所有命令都跑不了。先把这三个目录建好即可绕过。
heal_visualization_roots() {
  local day d
  day="$HOME/.codex/visualizations/$(date +%Y/%m/%d)"
  [ -d "$day" ] || return 0
  for d in "$day"/*/; do
    d=${d%/}
    [ -d "$d" ] || continue
    mkdir -p "$d/.git" "$d/.agents" "$d/.codex" 2>/dev/null
  done
}

# 单实例：顺手结束可能残留的旧伴随进程（脚本在宿主会话里运行时才生效）
for pid in $(pgrep -f "codex-usage-companion\.sh$" 2>/dev/null); do
  [ "$pid" = "$$" ] && continue
  [ "$pid" = "$PPID" ] && continue
  if kill "$pid" 2>/dev/null; then log "结束旧的伴随进程 $pid"; fi
done

log "伴随脚本启动（PID $$）"
while true; do
  heal_visualization_roots

  # Codex 主进程变了（含重启后换了新 PID）= 新的一轮，解除“用户已关闭”
  CUR_MAIN="$(main_pid)"
  if [ "$CUR_MAIN" != "$LAST_MAIN" ]; then
    if [ -n "$CUR_MAIN" ]; then
      USER_CLOSED=0
      log "检测到 Codex 主进程 $CUR_MAIN，允许打开窗口"
    fi
    LAST_MAIN="$CUR_MAIN"
  fi

  # 应用菜单点“Codex 用量窗口”时留下的请求文件
  if [ -f "$DIR/reopen.request" ]; then
    rm -f "$DIR/reopen.request"
    WID=$(xdotool search --class "[Cc]odex[Uu]sage" 2>/dev/null | head -1)
    if [ -n "$WID" ]; then
      xdotool windowmap "$WID" 2>/dev/null
      xdotool windowraise "$WID" 2>/dev/null
      xdotool windowactivate "$WID" 2>/dev/null
      log "恢复已存在的用量窗口 $WID"
    else
      USER_CLOSED=0
      WINDOW_PID=""
      log "收到手动打开请求"
    fi
  fi

  # 窗口自行退出（用户点了关闭）时不自动重开，等下一次 Codex 启动
  if [ -n "$WINDOW_PID" ] && ! kill -0 "$WINDOW_PID" 2>/dev/null; then
    log "用量窗口已退出，本轮不再自动重开"
    USER_CLOSED=1
    WINDOW_PID=""
  fi

  if app_running; then
    ensure_server || log "看板服务启动失败"
    if [ "$USER_CLOSED" = "0" ] && [ -z "$WINDOW_PID" ]; then
      # 强制 UTF-8 locale：否则 Tk 会把中文标题写成乱码属性
      "$DIR/window-impl.sh" >>"$LOG" 2>&1 &
      WINDOW_PID=$!
      log "打开用量窗口（PID $WINDOW_PID）"
    fi
  else
    if [ -n "$WINDOW_PID" ] && kill -0 "$WINDOW_PID" 2>/dev/null; then
      kill "$WINDOW_PID" 2>/dev/null
      log "Codex 已退出，关闭用量窗口"
    fi
    WINDOW_PID=""
    USER_CLOSED=0
  fi
  sleep 3
done
