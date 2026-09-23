#!/usr/bin/env bash
# 打包一个可以一键安装的 .deb
#
#   ./packaging/build-deb.sh            # 产物在 packaging/out/
#   版本号取自 chuang/__init__.py 的 __version__
#
# 产物不进仓库（见 .gitignore），上传到 Release 即可。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$SRC/packaging/out"
BUILD="$SRC/packaging/build"
PKG="chuang"
ARCH="all"
# 可以用环境变量覆盖（打包进 Release 前建议设成自己的）
PKG_MAINTAINER="${PKG_MAINTAINER:-chuang <noreply@example.com>}"
PKG_HOMEPAGE="${PKG_HOMEPAGE:-https://github.com/example/chuang}"

VERSION="$(python3 - "$SRC" <<'PY'
import re, sys, pathlib
text = (pathlib.Path(sys.argv[1]) / "chuang" / "__init__.py").read_text(encoding="utf-8")
print(re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1))
PY
)"

echo "▸ 打包 $PKG $VERSION ($ARCH)"
rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" \
         "$BUILD/opt/chuang" \
         "$BUILD/usr/bin" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/share/doc/$PKG"

# 程序本体
install -d "$BUILD/opt/chuang"
# 截图不进包体（README 里用在线版即可），程序 + 文档 + 工具脚本
cp -r "$SRC/chuang" "$SRC/chuang-gui" "$SRC/README.md" "$SRC/DESIGN.md" \
      "$SRC/CHANGELOG.md" "$SRC/LICENSE" "$SRC/tools" \
      "$BUILD/opt/chuang/"
find "$BUILD/opt/chuang" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$BUILD/opt/chuang" -name '*.pyc' -delete
chmod -R u=rwX,go=rX "$BUILD/opt/chuang"     # 目录 755、文件 644
chmod 755 "$BUILD/opt/chuang/chuang-gui"

# 命令、菜单项、图标
ln -sf /opt/chuang/chuang-gui "$BUILD/usr/bin/chuang"
install -m644 "$SRC/data/chuang.desktop" "$BUILD/usr/share/applications/chuang.desktop"
install -m644 "$SRC/data/chuang.svg" \
        "$BUILD/usr/share/icons/hicolor/scalable/apps/chuang.svg"
install -m644 "$SRC/README.md" "$BUILD/usr/share/doc/$PKG/README.md"
install -m644 "$SRC/LICENSE" "$BUILD/usr/share/doc/$PKG/copyright"

# 变更记录（Debian 政策要求压缩存放）
gzip -9nc "$SRC/CHANGELOG.md" > "$BUILD/usr/share/doc/$PKG/changelog.gz"

# control
INSTALLED_SIZE="$(du -sk "$BUILD" | cut -f1)"
cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $PKG_MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, python3-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, adwaita-icon-theme
Recommends: python3-xlib, gnome-shell-extension-appindicator
Homepage: $PKG_HOMEPAGE
Description: 窗 · Chuang —— 把你头顶此刻真实的天空搬到桌面
 桌面上一扇会自己变化的窗：太阳、月亮、星星与云雨都由本地天文算法
 与真实天气算出，窗台上那盆植物的影子方向与长短也随真实的太阳位置变化。
 支持把此刻的天空设为桌面壁纸、最小化到系统托盘、沉浸全屏。
EOF

# 安装后刷新菜单与图标缓存；卸载后清理配置（保留，只提示）
cat > "$BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  command -v update-desktop-database >/dev/null && \
    update-desktop-database -q /usr/share/applications || true
  command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor || true
fi
exit 0
EOF

cat > "$BUILD/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
  for p in $(pgrep -f '/opt/chuang/chuang-gui' 2>/dev/null) \
           $(pgrep -f 'bin/chuang$' 2>/dev/null); do
    kill "$p" 2>/dev/null || true
  done
fi
exit 0
EOF

cat > "$BUILD/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  command -v update-desktop-database >/dev/null && \
    update-desktop-database -q /usr/share/applications || true
  echo "提示：个人配置与缓存仍在 ~/.config/chuang 与 ~/.cache/chuang，"
  echo "      如需彻底清理可手动删除（purge 时它们会被一并移除）。"
fi
exit 0
EOF
chmod 755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/prerm" "$BUILD/DEBIAN/postrm"

# 允许本地构建时自动带上依赖（可选）
mkdir -p "$OUT"
DEB="$OUT/${PKG}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$BUILD" "$DEB" >/dev/null
echo "✓ 打好: $DEB  ($(du -h "$DEB" | cut -f1))"
echo "  安装: sudo dpkg -i $DEB"
dpkg-deb -I "$DEB" | sed -n '1,20p'
