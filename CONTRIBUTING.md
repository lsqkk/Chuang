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
make test                                   # 跑自动化测试（只用标准库）
```

天气与位置都能在界面上改（菜单 → 换一扇窗），但假装天气更快，适合反复调画面。

改完之后至少跑一遍 `make check`（语法 + 测试 + 出一张图）。改天文或壁纸的话，
`tests/` 里已经有对应的回归用例，加一条比在 issue 里描述现象有用得多。

如果装了 `pyflakes`（`python3 -m pyflakes chuang/*.py tests/*.py`），提交前顺手跑一遍
——它没进 CI（不想为一个小工具再拉一个依赖进来），但本地能挡住"import 了没用"
这类噪音。

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
chuang/wallpaper_ctl.py  桌面壁纸这一摊的调度（接管/跟随/动态/还原/诊断）
chuang/update.py      检查更新（GitHub Releases，纯标准库）
chuang/update_ui.py   下载 → 校验 → 安装 → 重启这条链路
chuang/actions.py     菜单与动作注册（想加一个入口，就看这一个文件）
chuang/dialogs.py     自绘的小窗口（换城市/跳到某一刻/详情/选择…）
chuang/diagnostics.py 诊断文本（纯函数，没有 GTK 也能测）
chuang/app.py         窗口本身：画面、交互、心跳、托盘与生命周期
tests/                自动化测试（含 gui_smoke.py：开窗才测得到的那部分）
```

想改画风，基本都在 `palette.py`（颜色关键帧）与 `render.py`；想改天象，在 `astronomy.py`。
`app.py` 里只剩"窗口本身"——1.1.8 之前它有两千行、同时当菜单表、壁纸调度器、
更新安装器与诊断器用；现在那些各自成文件，改动面小得多。

## 提交前的自检

```bash
python3 -m py_compile chuang/*.py tools/*.py chuang-gui   # 至少先能编译
python3 tools/make_screenshots.py                          # 画风变了就更新截图
./packaging/build-deb.sh                                   # 打包是否仍然通过
```

## 发布新版本

版本号只有一处：`chuang/__init__.py` 的 `__version__`。发版前改它，并在 `CHANGELOG.md`
顶部加一段同名的小节（Release 说明就取这一段）。

**平时：push 到 master 就完了。** `.github/workflows/release.yml` 会读版本号，发现它比
现有 tag 新时自动打 tag `vX.Y.Z`、打包 `.deb`、建 Release，把 `.deb` 与 `SHA256SUMS`
一起挂上去。版本号没涨的普通提交只打包自检，产物留在那次运行的 Artifacts 里，不发版。

想在本机把整条路盯着走完：

```bash
./packaging/release.sh --dry-run   # 只检查：版本涨了没、CHANGELOG 有没有这一段、树干不干净
./packaging/release.sh             # 打 tag 推上去 → 等 CI → 把 Release 上的 .deb 下回来验一遍
./packaging/release.sh --local     # 不走 CI：本地打包，用 gh 直接建 Release 并上传（补传/换包）
```

需要 GitHub CLI（`gh`）已登录（`gh auth login`）和能推送的 git 凭据
（`gh auth setup-git` 一条就够）。要补发或重跑，也可以去 Actions 页面手动运行
「发布 .deb」这个工作流。

## 约定

- **不要引入第三方 Python 库**：这个项目的一个卖点就是"只用系统自带的东西"。
- **机制类的结论只写在 `DESIGN.md` 一处**，代码注释里引用它（"为什么这么做"
  容易过时：1.1.4 改掉了壁纸的刷新机制，而三处注释还留着旧说法，
  下一个人很可能照着旧注释把坑再踩一遍）。
- 改判据（升落、暮光、视差、折射这类）时，**说清楚用的是哪个高度口径**：
  几何高度还是视高度。把两者混着比，误差就是"折射算了两遍"（日出会差 4 分钟）。
- 面向用户的文案用中文，代码注释用中文，标识符用英文。
- 新功能请顺手更新 `README.md` 与 `CHANGELOG.md`。
- 提交信息建议一句话说明"改了什么、为什么"，不必拘泥格式。

## 已知的取舍

- **不做"铺满桌面的窗口"式动画壁纸**：GNOME 的桌面图标画在系统图层，客户端窗口一定
  压住它，会毁掉桌面的可用性（见 README 的说明）。
- **托盘图标不提供 `IconName`，只用像素图**：这是在 GNOME 42 的 AppIndicator 扩展上
  实测最稳的方式（见 `tray.py` 里的注释）。
- 天文精度以"肉眼正确"为准，不追求历表级精度。实测口径（与 JPL 历表口径的
  pyephem、以及 USNO 的官方升落时刻对照）：太阳位置残差约 ±0.01°；
  月亮地心黄经残差 < 1′、距离残差 < 10 km，绘制时另作地平视差修正；
  四城 × 三季的日出/日落/中天/月出/月落与 USNO 相差 < 0.5 分钟。
  这些数字都钉在 `tests/test_astronomy.py` 里，改动会当场报警。
