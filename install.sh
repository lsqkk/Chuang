#!/usr/bin/env bash
# 把「窗」安装到系统：/opt/chuang + 菜单项 + 图标 + 命令 chuang
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/opt/chuang}"
BIN="/usr/local/bin/chuang"

# 如果已经用 .deb 装过，就别再叠一层源码安装（两者路径相同会互相覆盖）
if dpkg -s chuang >/dev/null 2>&1; then
  echo "检测到已通过 .deb 安装的「窗」。"
  echo "要改用源码安装，请先 sudo apt remove chuang，或直接用已安装的版本。"
  exit 1
fi

echo "▸ 检查依赖"
MISSING=""
for pkg in python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
done
if [ -n "$MISSING" ]; then
  echo "  需要安装：$MISSING"
  sudo apt-get install -y $MISSING
fi
# 置顶功能需要 python3-xlib（可选）
if ! python3 -c "import Xlib" >/dev/null 2>&1; then
  echo "  可选：安装 python3-xlib 以支持「窗口置顶」"
  sudo apt-get install -y python3-xlib || true
fi

echo "▸ 复制程序到 $PREFIX"
sudo mkdir -p "$PREFIX"
# 只清理本程序自己的旧文件，不动这个目录里的其他东西
sudo rm -rf "$PREFIX/chuang"
sudo rm -f "$PREFIX/chuang-gui"
sudo cp -r "$SRC/chuang" "$SRC/chuang-gui" "$SRC/README.md" "$SRC/DESIGN.md" \
  "$SRC/CHANGELOG.md" "$SRC/LICENSE" "$SRC/tools" "$PREFIX/"
sudo find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} +
sudo chmod -R u=rwX,go=rX "$PREFIX"
sudo chmod +x "$PREFIX/chuang-gui"

echo "▸ 注册命令与菜单项"
sudo ln -sf "$PREFIX/chuang-gui" "$BIN"
sudo install -Dm644 "$SRC/data/chuang.svg" \
  /usr/share/icons/hicolor/scalable/apps/chuang.svg
sudo install -Dm644 "$SRC/data/chuang.desktop" \
  /usr/share/applications/chuang.desktop
sudo update-desktop-database -q /usr/share/applications || true
sudo gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor || true

echo "✓ 装好了。终端输入 chuang 打开，或在应用菜单里找「窗」。"
