#!/usr/bin/env bash
# 安装 codex-token：把源码放到 ~/.local/share/codex-usage，并创建命令、菜单入口。
#   ./install.sh                 仅安装
#   ./install.sh --autostart     顺便装开机自启（登录后等 Codex 出现再开窗）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CODEX_USAGE_HOME:-$HOME/.local/share/codex-usage}"
BIN="${CODEX_USAGE_BIN:-$HOME/.local/bin}"

echo "源码目录: $SRC"
echo "安装目录: $DEST"

mkdir -p "$DEST" "$BIN" "$HOME/.local/share/applications"

FILES="codex-usage.mjs dashboard-server.mjs dashboard.html usage-window.py usage-window-gtk.py window-impl.sh codex-usage-companion.sh codex-token codex-token-window README.md"
if [ "$SRC" = "$DEST" ]; then
  echo "源码目录就是安装目录，跳过复制"
else
  for f in $FILES; do
    install -m 0755 "$SRC/$f" "$DEST/$f"
  done
  chmod 0644 "$DEST/README.md" "$DEST/dashboard.html" "$DEST/dashboard-server.mjs" \
             "$DEST/usage-window.py" "$DEST/usage-window-gtk.py" "$DEST/codex-usage.mjs"
fi

# 命令行入口指向安装目录里的同一份脚本
ln -sf "$DEST/codex-token" "$BIN/codex-token"
ln -sf "$DEST/codex-token-window" "$BIN/codex-token-window"
# 兼容旧命令名（历史习惯/脚本里可能还在用）
ln -sf "$DEST/codex-token" "$BIN/codex-usage"
ln -sf "$DEST/codex-token-window" "$BIN/codex-usage-window"

cat > "$HOME/.local/share/applications/codex-usage-window.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Codex 用量窗口
Comment=实时查看 Codex 的 token 消耗与缓存命中率
Exec=$BIN/codex-token-window
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;Monitor;
DESKTOP

if [ "${1:-}" = "--autostart" ]; then
  mkdir -p "$HOME/.config/autostart"
  cat > "$HOME/.config/autostart/codex-usage-companion.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Codex 用量伴随窗口
Comment=Codex 启动时自动显示实时 token 用量与缓存命中率
Exec=$DEST/codex-usage-companion.sh
Icon=utilities-system-monitor
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
  echo "已写入开机自启：~/.config/autostart/codex-usage-companion.desktop"
fi

command -v desktop-file-validate >/dev/null 2>&1 && \
  desktop-file-validate "$HOME/.local/share/applications/codex-usage-window.desktop" 2>&1 | head -2 || true

cat <<TIPS

安装完成。用法：
  codex-token            终端面板（--all / --json / --compact / --help）
  codex-token-window     打开伴随窗口（应用菜单里也有「Codex 用量窗口」）
  旧命令名 codex-usage / codex-usage-window 仍然可用（兼容链接）
  $DEST/dashboard-server.mjs   浏览器看板，然后开 http://127.0.0.1:8788/

想要登录后自动跟随 Codex 开窗，重跑一次：./install.sh --autostart
TIPS
