#!/usr/bin/env bash
# 窗口实现选择。
# 默认用 Tk 无边框版：这台机器上 GNOME 会按住常规窗口（Tk 和 Python+GTK 都试过，
# WM_STATE 停在 Iconic 不映射），只有 override-redirect 窗口能正常显示。
# 设置 CODEX_USAGE_IMPL=gtk 可以强制走 GTK 版（在正常环境下体验更好）。
DIR="$HOME/.local/share/codex-usage"
export LC_ALL=C.UTF-8 LANG=C.UTF-8
if [ "${CODEX_USAGE_IMPL:-}" = "gtk" ]; then
  exec python3 "$DIR/usage-window-gtk.py"
fi
exec python3 "$DIR/usage-window.py"
