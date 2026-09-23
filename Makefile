# 常用任务：make help 看一眼
.PHONY: help run city install deb release release-dry screenshots test check clean uninstall

PY ?= python3

help:
	@echo "窗 · Chuang"
	@echo "  make run          直接运行（开发模式）"
	@echo "  make city         启动并打开「换一扇窗」"
	@echo "  make screenshots  重新生成 README 里的截图"
	@echo "  make test         跑自动化测试（天文、天气、壁纸、绘制冒烟）"
	@echo "  make check        语法检查 + 跑测试 + 出一张测试图"
	@echo "  make deb          打包 .deb（产物在 packaging/out/）"
	@echo "  make release      发版：打 tag → CI 打包 → 挂到 Release（packaging/release.sh）"
	@echo "  make release-dry  发版前的检查（--dry-run，什么都不改）"
	@echo "  make install      从源码安装（需要 sudo）"
	@echo "  make uninstall    卸载源码安装"
	@echo "  make clean        清掉 pycache 与打包中间产物"

run:
	./chuang-gui

city:
	./chuang-gui --city

screenshots:
	$(PY) tools/make_screenshots.py

test:
	$(PY) -m unittest discover -s tests -t . -v

check:
	$(PY) -m py_compile chuang/*.py tools/*.py chuang-gui
	$(PY) -m unittest discover -s tests -t .
	$(PY) tools/snapshot.py /tmp/chuang-check.png 18:35 34.34 108.94
	@echo "✓ 检查通过"

deb:
	./packaging/build-deb.sh

release:
	./packaging/release.sh

release-dry:
	./packaging/release.sh --dry-run

install:
	./install.sh

uninstall:
	sudo rm -rf /opt/chuang /usr/local/bin/chuang \
	  /usr/share/applications/chuang.desktop \
	  /usr/share/icons/hicolor/scalable/apps/chuang.svg
	@echo "已卸载（个人配置保留在 ~/.config/chuang）"

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf packaging/build
	@echo "已清理"
