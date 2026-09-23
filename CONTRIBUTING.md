# 参与开发

谢谢你愿意看这份代码。下面是几条最短路径。

## 跑起来

```bash
git clone <this-repo> && cd Chuang
./chuang-gui                 # 不需要安装，直接跑
```

依赖只有系统包（见 README「依赖」一节），没有 pip 依赖，也没有构建步骤。

## 改代码时的两个顺手工具

```bash
CHUANG_TIME=21:30 ./chuang-gui              # 把"此刻"假装成 21:30
CHUANG_WEATHER=63:95:18:200 ./chuang-gui    # 假装天气：中雨 / 云量95% / 风18km-h / 200°
python3 tools/snapshot.py out.png 18:35 34.34 108.94   # 不开窗口直接出一张 PNG
python3 tools/make_screenshots.py           # 重新生成 README 里的截图
```

天气与位置都能在界面上改（菜单 → 换一扇窗），但假装天气更快，适合反复调画面。

## 代码分层

```
chuang/astronomy.py   本地天文计算（太阳 NOAA、月亮 Meeus、恒星、升落）
chuang/palette.py     天色色板：太阳高度角 → 天顶色/地平色/辉光/环境光
chuang/scene.py       时刻＋地点＋天气 → 一帧场景；今日天色长卷
chuang/weather.py     Open-Meteo 客户端、缓存、离线降级
chuang/render.py      Cairo 绘制（天空、云雨、剪影、街景、窗台、长卷、信息卡）
chuang/street.py      行人与车辆（位置 = 时间的函数）
chuang/tray.py        托盘：KStatusNotifierItem + DBusMenu
chuang/wallpaper.py   壁纸渲染线程与 GNOME 动态壁纸 XML
chuang/app.py         GTK4 界面与生命周期
```

想改画风，基本都在 `palette.py`（颜色关键帧）与 `render.py`；想改天象，在 `astronomy.py`。

## 提交前的自检

```bash
python3 -m py_compile chuang/*.py tools/*.py chuang-gui   # 至少先能编译
python3 tools/make_screenshots.py                          # 画风变了就更新截图
./packaging/build-deb.sh                                   # 打包是否仍然通过
```

## 约定

- **不要引入第三方 Python 库**：这个项目的一个卖点就是"只用系统自带的东西"。
- 面向用户的文案用中文，代码注释用中文，标识符用英文。
- 新功能请顺手更新 `README.md` 与 `CHANGELOG.md`。
- 提交信息建议一句话说明"改了什么、为什么"，不必拘泥格式。

## 已知的取舍

- **不做"铺满桌面的窗口"式动画壁纸**：GNOME 的桌面图标画在系统图层，客户端窗口一定
  压住它，会毁掉桌面的可用性（见 README 的说明）。
- **托盘图标不提供 `IconName`，只用像素图**：这是在 GNOME 42 的 AppIndicator 扩展上
  实测最稳的方式（见 `tray.py` 里的注释）。
- 天文精度以"肉眼正确"为准（太阳 ±0.01°，月亮 ±10"），不追求历表级精度。
