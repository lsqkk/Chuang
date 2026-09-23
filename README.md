# 窗 · Chuang

**把你头顶此刻真实的天空，搬到桌面的一扇窗里。**

*A live window to the real sky above you — sun, moon, stars, weather and the shadow on the windowsill, all correct.*

![窗 · Chuang](screenshots/11-wallpaper.png)

![license](https://img.shields.io/badge/license-MIT-blue)
![platform](https://img.shields.io/badge/platform-Ubuntu%2022.04%20%7C%20GNOME%2042-6a4c93)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![deps](https://img.shields.io/badge/dependencies-%E4%BB%85%20PyGObject-2f855a)

---

## 这是什么

它不是天气预报，也不是星象软件，而是**桌面上的一扇窗**：打开就对了，没有账号、
没有向导、没有需要你操心的地方。

- 太阳、月亮（连相位与亮面朝向）、一千六百多颗星星，全部由**本地天文算法**算出，
  断网也完全正确；
- 有网络时**镜像真实天气**：外面下雨，窗上就下雨；外面阴天，太阳只剩一个隐约的亮斑；
  起雾时远处的屋顶真的会糊掉；
- 窗台上那盆植物的**影子方向和长短，由真实的太阳方位角与高度角决定**——
  早晨影子长，正午最短，阴天没有影子。

它适合被"不时瞄一眼"：在无窗的房间待久了、想出门拍照、深夜加班想知道外面下没下雨。

## 亮点

| | |
|---|---|
| **今日天色** | 窗底那条细长光带是今天 00:00–24:00 天色的连续变化。**可以用鼠标拖动它"时间旅行"**，预览今晚 21:00 天是什么颜色——太阳高度、剩余天光、月相、云的多少都会跟着那一刻变。|
| **此刻的事实** | 按空格调出：日出日落、还剩多少天光、金色时刻、月相月出、太阳方位高度、此刻天气，外加一句人话。|
| **街上有人** | 地平线下那条街上有行人、自行车、汽车、公交车。下雨时行人撑伞、骑车的人变少；夜里车灯自动亮起；深夜行人稀疏、早晚高峰车更多。他们的位置是**时间的函数**，所以连续移动，同一时刻永远画成同一个样子。|
| **一扇能挂起来的窗** | 可以设为桌面壁纸，也可以缩窄后置顶在屏幕角落当"桌面夜灯"。|
| **收进托盘** | 关窗时问一次"最小化到托盘还是退出"并记住；托盘菜单与应用菜单**同源**，勾选状态天然一致。|

## 截图

| 清晨 · 金色时刻 | 上午 · 大致晴朗 | 正午 · 多云 |
|---|---|---|
| ![清晨](screenshots/01-dawn.png) | ![上午](screenshots/02-morning.png) | ![正午](screenshots/03-daylight.png) |

| 傍晚 · 金色时刻 | 暮色 · 下雨 | 夜 · 雨中车灯 |
|---|---|---|
| ![傍晚](screenshots/04-golden-hour.png) | ![暮色](screenshots/05-dusk-rain.png) | ![夜雨](screenshots/06-night-rain.png) |

| 阴天 | 雪 | 雾 |
|---|---|---|
| ![阴天](screenshots/07-overcast.png) | ![雪](screenshots/08-snow.png) | ![雾](screenshots/09-fog.png) |

| 宽幅：一扇横窗 | 作为桌面壁纸 |
|---|---|
| ![宽幅](screenshots/10-panorama.png) | ![壁纸](screenshots/11-wallpaper.png) |

> 截图由程序自己的渲染器离屏生成，可一键重跑：`python3 tools/make_screenshots.py`

## 安装

### 方式一：`.deb` 一键安装（推荐）

从 [Releases](../../releases) 下载 `chuang_1.0.0_all.deb`：

```bash
sudo dpkg -i chuang_1.0.0_all.deb
sudo apt-get -f install      # 万一缺依赖，补一下
```

装完在应用菜单里搜「窗」，或终端输入 `chuang`。卸载：`sudo apt remove chuang`。

### 方式二：从源码安装

```bash
git clone https://github.com/lsqkk/Chuang.git && cd Chuang
./install.sh                 # 需要时用 sudo
```

它会装到 `/opt/chuang`，并在 `/usr/local/bin/chuang` 放一个命令、注册菜单项与图标。
卸载：

```bash
sudo rm -rf /opt/chuang /usr/local/bin/chuang \
  /usr/share/applications/chuang.desktop \
  /usr/share/icons/hicolor/scalable/apps/chuang.svg
```

### 依赖

只用系统自带的组件，**没有第三方 Python 库**：

```
python3-gi  python3-gi-cairo  python3-cairo  gir1.2-gtk-4.0  gir1.2-adw-1
可选：python3-xlib（用于"窗口置顶"）
```

## 使用

| 操作 | 效果 |
|---|---|
| 拖动底部「今日天色」长卷 | 时间旅行，预览任意时刻的天空 |
| 点顶部提示条 / 按 `Esc` | 回到此刻 |
| 光标在长卷上滚滚轮 | 前后微调 10 分钟 |
| `空格` | 显示 / 隐藏「此刻的事实」 |
| `F11` | 沉浸全屏 |
| 右上角图钉 | 窗口置顶（缩窄后放在角落当"电子窗景"）|
| 右上角菜单 | 分四组：看（全屏/置顶/事实）、换（城市/天气）、**桌面壁纸 ▸**、关闭方式 |

### 放到桌面上

> 先说清楚：**GNOME 没有"动画壁纸"的接口**。壁纸永远是一张静态图，系统只在换图时重画一次。
> 想让它动，只有两条路：不断换图（第 1 种），或者用一个铺满桌面的窗口自己画——
> 后者会把**桌面图标和右键菜单盖住**（GNOME 的桌面图标画在系统图层里，客户端窗口一定压不过它），
> 所以「窗」**不做**那种方案。

1. **壁纸跟随此刻（每 10 秒换一张）** — 时间、天色、街上的人车都跟着走。
   为了绕开 GNOME「同一路径不重新加载」的脾气，程序在两个文件名之间交替写入。
   **需要「窗」留在托盘里**：菜单 → 关窗时：最小化到托盘。
   代价：留一颗核约 6% 用于绘制，外加每次 16 KB 的系统设置写入。
2. **离线动态壁纸（关掉程序也有效）** — 把今天 24 小时画成 96 张天色
   （每 15 分钟一张，用当天逐时预报算云量），写成 GNOME 原生的 `<background>` 定时壁纸。
   不用开着程序，桌面也会从清晨走到夜里；代价是粒度粗，看着是"天色在变"而不是连续动画。
3. **单张快照** — 把此刻的天空设为壁纸，之后不再变化。

两种都可以配合 **壁纸上显示「此刻的事实」** 与 **「今日天色」长卷** 两个开关。
第一次改壁纸前会记下原来那张，菜单里的**还原成原来的壁纸**随时能换回去。

## 常见问题

**为什么壁纸里的行人是一跳一跳的，而应用窗口里是连续走的？**
因为壁纸是一张静态图，只能靠"换图"制造变化。应用窗口是每秒 10 帧连续绘制，
所以那里的人车走得顺滑；壁纸每 10 秒换一张，人车看上去就是一秒一步地"顿"过去。

**托盘图标不见了？**
「窗」的托盘依赖 GNOME 的 AppIndicator 扩展（Ubuntu 默认开启）。如果扩展曾被别的程序
弄崩而被禁用，需要重启一次 GNOME Shell：按 `Alt+F2`，输入 `r`，回车。
（程序内部有自动重试：扩展恢复后它会自己重新注册，不必重启「窗」。）

**没网会怎样？**
完全可用。天空、日月星、影子全部本地计算，只是不知道有没有云；信息卡会显示
「未联网 · 仅天文模式」，有历史缓存时会继续显示上次的天气并标注。

**它会不会很吃资源？**
窗口可见时约一颗核的 10%（有降水时略高），窗口不在前台时降到约五分之一，
收进托盘后完全不绘制。壁纸跟随模式额外约 6%（每 10 秒一次，在后台线程里做）。

**城市怎么换？**
菜单 → 换一扇窗（城市），联网搜索即可；也可以直接填经纬度（按经度取整时区）。

## 数据与隐私

- 天空：**本地计算**，不出网。
- 天气：只把经纬度发给 [Open-Meteo](https://open-meteo.com/)（免费、无需 API Key），
  结果缓存在 `~/.cache/chuang/weather.json`；断网自动退回"纯天文模式"。
- 位置：存在 `~/.config/chuang/config.json`，仅本机。
- 没有账号、没有统计、没有任何上传。

## 开发

```bash
./chuang-gui                       # 直接跑（开发模式）
./chuang-gui --city                # 启动时直接打开"换一扇窗"
CHUANG_TIME=21:30 ./chuang-gui     # 把"此刻"假装成 21:30
CHUANG_WEATHER=63:95:18:200 ./chuang-gui   # 假装成 中雨/云量 95%/风 18km-h/200°
python3 tools/make_screenshots.py  # 重新生成 README 里的截图
python3 tools/snapshot.py a.png 18:35 34.34 108.94     # 不开窗口直接出一张图
./packaging/build-deb.sh           # 打 .deb（产物在 packaging/out/，不进仓库）
make help                          # 常用任务
```

代码分层（`chuang/`）：

| 文件 | 职责 |
|---|---|
| `astronomy.py` | 本地天文：太阳（NOAA）、月亮（Meeus 第 47 章）、恒星、升落与暮光 |
| `palette.py` | 以太阳高度角为唯一驱动量的天色色板（线性光空间插值） |
| `scene.py` | 把「时刻＋地点＋天气」组装成一帧，并生成今日天色长卷 |
| `weather.py` | Open-Meteo 客户端、缓存与离线降级 |
| `render.py` | 全部 Cairo 绘制：天空、云雨、剪影、街景、窗台与影子、长卷、信息卡 |
| `street.py` | 行人与车辆（位置是时间的函数，因此在动态壁纸里也连贯） |
| `tray.py` | 系统托盘（KStatusNotifierItem + DBusMenu，纯 Gio 实现） |
| `wallpaper.py` | 壁纸渲染线程、GNOME 动态壁纸 XML、设置与还原 |
| `app.py` | GTK4 界面、菜单、交互与生命周期 |

改代码时有两个顺手的诊断口子：环境变量 `CHUANG_TIME`（假装时刻）与 `CHUANG_WEATHER`
（假装天气），以及 `tools/snapshot.py`——不用开窗口就能把任意天气任意时刻画成 PNG。
更多约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

- 天气数据：[Open-Meteo](https://open-meteo.com/)，免费且无需 API Key。
- 星表：由 [d3-celestial](https://github.com/ofrohn/d3-celestial) 的星表裁剪而来
  （星等 ≤ 5.0，约 1600 颗），构建脚本见 `tools/build_stars.py`。
- 太阳位置采用 NOAA Solar Calculator 的算法；月亮位置采用 Jean Meeus
  《Astronomical Algorithms》第 47 章的截断版。

## 许可

[MIT](LICENSE)
