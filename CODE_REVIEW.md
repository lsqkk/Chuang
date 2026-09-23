# 「窗 · Chuang」代码审阅报告

> 审阅对象：`chuang/`（约 8 700 行 Python）、`tools/`、`packaging/`、`.github/workflows/`
> 版本：`1.1.7`（`chuang/__init__.py`）
> 审阅日期：2026-09-23　环境：Ubuntu 22.04 / GNOME 42 / X11 / GTK 4.6 + libadwaita 1.1.7
> 审阅方式：全量读码 + 只读实测（离屏渲染、天文数值对照、缓存与 D-Bus 路径复现）
> **本次审阅没有修改任何一行业务代码，只新增了本文件。**

---

## 0. 一句话结论

这是一个**自我要求明显高于同类个人项目**的作品：分层干净、没有第三方 Python 依赖、
调试口子和诊断入口都齐全、文档与 CI 的工程习惯罕见地好。
但它目前有 **三类真实缺陷** 需要优先处理：

1. **承诺与行为不一致**——「跟随真实天气」这个开关实际上关不掉天气（缓存永远在画）；
2. **有明确可达的崩溃路径**——托盘图标被 `Activate` 时抛 `TypeError`；
3. **核心卖点「诚实」被打了折扣**——画出来的月亮没有做地平视差修正，最多偏约 1.0°
   （≈2 个视月面直径），会出现「月亮其实还没升起来，画面上已经有了」。

除此之外，剩下的是缓存键、桌面状态还原、自更新校验、无测试、文档滞后这类
「不会立刻炸、但迟早会咬人」的问题。

---

## 1. 问题清单（按重要程度）

分级口径：

- **P1（严重）**：用户能直接感知的功能失效 / 与文档承诺相反 / 主要交互路径报错。
- **P2（中等）**：正确性或数据有偏差、破坏用户环境、安全与流程缺口，需要排期修。
- **P3（轻微）**：可维护性、可访问性、文案与零碎瑕疵。

| # | 级别 | 维度 | 问题 | 证据位置 |
|---|---|---|---|---|
| P1-1 | P1 | 功能 | 关闭「跟随真实天气」后，画面里**仍然有天气**（磁盘缓存一直在用） | `app.py:466,1624`、`weather.py:276`、`scene.py:222` |
| P1-2 | P1 | 功能 | 托盘图标 `Activate`（双击图标 / 键盘激活）抛 `TypeError`，窗口不会被叫到前台 | `tray.py:396,399` ↔ `app.py:1458` |
| P1-3 | P1 | 正确性 | 绘制的月亮是**地心坐标**，缺地平视差修正，最多高约 1.0° | `astronomy.py:78-93,325`、`render.py:659` |
| P2-1 | P2 | 产品/环境 | 接管壁纸时会**永久改掉**用户的 `picture-options`（缩放进 `zoom`），「还原」不还原它 | `wallpaper.py:205-212,259` |
| P2-2 | P2 | 功能 | 离屏缓存键里没有城市种子；换城市后可能继续显示**上一个城市的天际线** | `render.py:1005-1018`、`app.py:1566` |
| P2-3 | P2 | 安全 | 自动更新直接下载 `.deb` 并 `sudo` 安装，**不校验** Release 上已有的 `SHA256SUMS` | `app.py:1212-1290`、`update.py:99` |
| P2-4 | P2 | 产品/文案 | 主动关掉天气的用户，信息卡会写「**未联网** · 仅天文模式」 | `render.py:1624` |
| P2-5 | P2 | 正确性 | `solar_noon()` 定点迭代在高太阳高度下发散，夏季最多差 **2 小时 46 分** | `astronomy.py:145` |
| P2-6 | P2 | 正确性 | 手输经纬度时用 `Etc/GMT±N` 固定偏移，**不认夏令时**（欧美城市夏季差 1 小时） | `app.py:414-418` |
| P2-7 | P2 | 工程化 | 全仓 **零自动化测试**，CI 只打包不跑任何检查（无 lint / 无类型检查） | 全仓无 `tests/`、`.github/workflows/release.yml` |
| P2-8 | P2 | 文档 | 多处文档仍在描述**已被证伪/已废弃**的壁纸机制 | `wallpaper.py:1-7`、`DESIGN.md §4.1`、`README.md`、`AGENTS.md §7` |
| P3-1 | P3 | 健壮性 | 生成动态壁纸时**先删旧帧再生成**，中途失败会留下指向已删文件的 XML（桌面变空） | `wallpaper.py:380-386` |
| P3-2 | P3 | 代码 | `wallpaper.py` 里 `set_wallpaper` 定义了两次（148 行、416 行） | `wallpaper.py:148,416` |
| P3-3 | P3 | 架构 | `app.py` 2 020 行，`ChuangWindow` 一个类扛了 8 种职责 | `app.py` |
| P3-4 | P3 | 产品 | 多显示器只按**主屏**比例作画，其它屏幕被拉伸/裁切 | `wallpaper.py:46-62` |
| P3-5 | P3 | 产品 | 全部文案硬编码中文，没有 gettext / i18n | 全仓 |
| P3-6 | P3 | 可访问性 | 信息卡画在画布上，屏幕阅读器读不到；快捷键只有一半有菜单提示 | `render.py:1627+` |
| P3-7 | P3 | 产品 | 首启城市只弹一条 toast，没有「确认这扇窗朝哪」的引导步骤 | `app.py:1770-1782` |
| P3-8 | P3 | 性能 | 1080p 下雨时实测 22.9 ms/帧（≈1/3 个核），README 只写「约 10%」 | 实测见附录 A |
| P3-9 | P3 | 代码 | `moon_phase()` 的文档说第三个返回值是「亮面方向」，实际是距离角且调用方全部丢弃 | `astronomy.py:328-345` |
| P3-10 | P3 | 架构 | 配置没有 schema 版本号，历史字段删除后只能靠 `sanitize()` 兜 | `config.py:96-130` |
| P3-11 | P3 | 代码 | 未使用的 import、`chuang --help` 不存在、未知参数被静默忽略 | `wallpaper.py:15`、`weather.py:15`、`chuang-gui` |

---

## 2. P1 详细分析

### P1-1　「跟随真实天气」关不掉天气（缓存永远在画）

**现象。** 菜单里点掉「跟随真实天气」，toast 也会说「只看天，不看天气」，但窗上的云、雨、
信息卡里的「窗外」一行照样在，重启之后依旧是老样子，用户会以为开关坏了。

**为什么。** 这个开关只切断了「联网刷新」，没有切断「用来作画的那份数据」：

```python
# weather.py:276  ——  启动时无条件把磁盘缓存读进来，跟 enabled 无关
self.weather: Weather | None = load_cache()

# app.py:466  ——  只把 enabled 设成 False
self.weather.enabled = self.config.mirror_weather

# app.py:1624  ——  作画时永远把那份缓存塞给场景
self._scene = self.engine.build(when, self.weather.weather, ...)

# scene.py:222  ——  只要 ok 就当有天气
if weather is not None and weather.ok:
```

`enabled = False` 只影响 `maybe_refresh()` / `refresh()`（`weather.py:287-300`），
而 `_act_weather()`（`app.py:896-906`）也没有清空 `self.weather.weather`。
于是：**只要这台机器成功抓取过一次天气，这个开关就永久失效**——包括重启之后，
因为缓存文件还在 `~/.cache/chuang/weather.json`。

**实测复现**（用临时缓存目录，没碰用户数据）：

```text
关掉之后 service.weather 还是: True ok= True
→ 关掉天气后，画面里仍然有天气: True 云量 95.0 天气码 63 中雨
```

**影响。** 这是「基本可用性」级别的失信：用户明确表达了意图，系统没有照做，
而且**永远不会自愈**（不再联网，也就永远不会再刷新成别的东西）。
README 里那句「只看天，不看天气」目前是假的。

**建议。**

1. 让 `WeatherService` 暴露 `effective` 属性（`enabled ? weather : None`），
   或在 `_act_weather(False)` 与 `__init__`（`enabled=False` 时）清空 `weather`；
2. `_current_scene()` / `_refresh_ribbon()` / `apply_wallpaper()` 一律改用 `effective`；
3. 加一条自动化测试：`enabled=False` + 有缓存 → `Scene.has_weather is False`。

### P1-2　托盘图标的 `Activate` 会抛 `TypeError`

**现象。** 双击托盘图标（GNOME 扩展里双击 = 调用 `Activate`）不会把窗口叫到前台，
进程日志里多一条 traceback。关窗时那句提示「托盘图标点一下就能再打开」也不准。

**证据。** 托盘把回调直接接到窗口方法上，但那个方法要求一个必填参数：

```python
# app.py:1443
self.tray = traymod.Tray(self.menu_model, self._tray_activate, self._tray_lookup, ...)
# app.py:1458
def _tray_activate(self, name: str, target=None):
# tray.py:394-400  ——  这里一个参数都没传
if method in ("Activate", "SecondaryActivate"):
    self.activate_cb()
```

实测（真类 + 假 invocation）：

```text
EXCEPTION in Activate handler: TypeError W._tray_activate() missing 1 required positional argument: 'name'
```

而这条路径**确实会被走到**——本机扩展源码
`/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com/indicatorStatusIcon.js:250-255`
把左键双击转成 `this._indicator.open(x, y)`，`open()` 里就是
`this._proxy.ActivateRemote(...)`（同目录 `appIndicator.js:374-384`）。

**影响。** ① 一条主要交互路径静默失效；② D-Bus 方法处理器抛异常意味着**这一次调用
永远不会有回复**；③ 它污染了 `AGENTS.md §7` 要求「应为空」的日志检查。

**建议。**

```python
def _tray_activate(self, name: str = "win.show", target=None):
    self.activate(name, target)
```

并让 `tray._on_sni_call` 的 `Activate/SecondaryActivate` 分支显式传 `"win.show"`；
把「托盘图标点一下就能再打开」改成「左键点图标打开菜单 → 第一项是『显示「窗」』」。

### P1-3　画出来的月亮缺地平视差修正（最多偏约 1°，≈2 个月面直径）

**现象。** 月亮靠近地平线时，画面上它已经「在地平线上」，但实际那一刻它还在
地平线**以下**——画出了一个此刻看不见的月亮。

**为什么。** 月亮距离只有约 38 万公里，地平视差可达 57′～61′：

```python
# astronomy.py:78-93  ——  只有折射修正，没有视差修正
alt_d = alt * RAD
return alt_d + refraction(alt_d), az_d
```

同一份代码在算月出月落时用的是**标准的 h0 = +0.125°**（`astronomy.py:23,428-429`，
Meeus 的 `0.7275π − 34′`；这个口径要配地心坐标用，所以**升落时刻是对的**），
但绘制位置直接用 `moon_altaz()` 的地心高度，两者口径不一致。

**实测**（2026-09-23 09:02 UTC，西安）：

```text
月心高度(代码绘制用, 地心)=0.069°   地心距离=362110 km
地平视差=1.009°（60.6′，约 1.9 个视月面直径）
实际可见的天顶位置应为 -0.941°，差 1.009°
```

**影响。** 这是最贵的那种问题：这个项目的记忆点就是「它是对的」。
太阳视差 8.8″ 可以忽略，恒星更小，唯独月亮是**肉眼可辨**的量级
（1° ≈ 两个月亮直径，在地平线附近相当于约 4 分钟的赤道自转）。
`CONTRIBUTING.md` 写的「月亮 ±10″」只成立于**地心**位置，容易让人以为画面也这么准。

**建议。** 绘制时减去视差（`Δalt = −asin(R_earth/Δ)·cos(alt)`，把
`Equatorial.distance` 用起来），地平遮挡判断一并修正；文档改成
「月亮位置：地心 ±10″，绘制已作地平视差修正（残余 < 0.05°）」。

---

## 3. P2 详细分析

### P2-1　接管壁纸会永久改掉用户的 `picture-options`

`_set_background()` 每次都会写 `picture-options = zoom`（`wallpaper.py:205-212`），
而 `_remember_wallpaper()` 只记了 `picture-uri` / `picture-uri-dark` 两个值
（`app.py:922-938`），`restore()` 也只回写这两个（`wallpaper.py:259-269`）。

结果：一个原本用「拉伸 / 居中 / 平铺」的用户，点一次「把此刻的天空设为壁纸」之后，
即使点了「还原成原来的壁纸」，**缩放方式也永远变成 `zoom`**——他没同意过这件事。
这跟 `AGENTS.md §4`「不要动用户的桌面状态……测完必须原样还原」是同一类风险。

建议：接管前把 `picture-options` 一起记进配置（`prev_wallpaper_options`），
`restore()` 一并还原；并且只在真的要显示这张天空时设 `zoom`，
用户自己换壁纸时不写任何 `gsettings`。

### P2-2　离屏缓存键没有城市种子，换城市可能还挂着旧城市的天际线

城市那一层是「整帧里最贵的一块」，所以按光照档位缓存（`render.py:1005-1018`），
但 `key` 只有尺寸与光照量化值，**没有城市种子**；而 `_set_location()` 只清了
`painter._skyline`（层数据），没有清 `_city_surf` / `_sill_surf`
（`app.py:1566`）。壁纸线程那份 `Worker.painter` 更彻底：`Worker.location()`
只换引擎坐标，缓存一个都不清（`wallpaper.py:308-319`）。

实测（同坐标、只换名字）：

```text
same coords, different city name -> cached surface reused: True
```

也就是说：**新城市的「楼数据」已经生成好了，但画出来的还是旧城市的缓存位图**，
要等光照量化档位变化（太阳走一格）才刷新。实测两个相距约 20 km 的城市
（西安 → 咸阳）刚好把档位顶过一格，所以现实里多数时候看不出来——
但这属于「靠巧合正确」，而壁纸模式恰恰是运行时间最长、最容易被撞见的场景。

建议：把 `skyline_seed` 的返回值并进 `_city_key`，并在 `_set_location()` 里显式清掉
`_city_surf / _sill_surf / _sky_surf`（或给 `SkyPainter` 一个
`invalidate_location()`，让 `Worker.location()` 也调它）。

### P2-3　自更新不校验 `SHA256SUMS`

CI 已经把 `SHA256SUMS` 挂在 Release 上（`release.yml` 的 build 阶段），
但客户端完全没用上：`update.py` 只挑出 `.deb` 的 URL（`update.py:98-100`），
`app.py` 直接 `urlopen` 写到文件、然后 `sudo apt-get install -y -- <path>`
（`app.py:1212-1290`）。

HTTPS 挡住了传输层篡改，但「从 GitHub 拿一个二进制并用 root 安装、却不做任何
内容完整性校验」仍是这个项目价值最高的攻击面（Release 资产被替换、或用户网络里
有 TLS 拦截代理时都会中）。校验文件就躺在同一个 Release 里，
加这一步成本几乎为零：下载时顺带取 `SHA256SUMS`（`rel.assets` 里已有），
`hashlib.sha256` 比对通过再交给 `sudo`；不匹配就拒绝安装并显示两个摘要。

顺带一个小问题：`target = self._download_dir() / rel.deb_name`（`app.py:1236`）
直接拿远端文件名拼路径，建议先 `Path(rel.deb_name).name` 过一遍防 `../`
（当前仓库自控，风险低，但不该留着这种写法）。

### P2-4　主动关掉天气的用户，会看到「未联网」

```python
# render.py:1620-1624
if scene.has_weather:
    ...
else:
    rows.append(("窗外", "未联网 · 仅天文模式"))
```

`has_weather=False` 有两个完全不同的成因：真没网、以及**用户自己关掉了天气**。
后者看到「未联网」会去查网络，这是把人往错的方向指。
建议给 `Scene` 加 `weather_enabled`（或由 app 层把文案传进来），分别显示
「未联网 · 仅天文模式」/「你关掉了天气 · 只看天」。

### P2-5　`events["noon"]` 是错的（现在没人用，所以还没炸）

`solar_noon()` 用「把方位角迭代到 180°」的定点迭代求中天（`astronomy.py:145-153`）。
太阳接近天顶时方位角变化极快，迭代会发散：

| 日期（西安） | 暴力扫描真值 | `solar_noon()` | 误差 |
|---|---|---|---|
| 2026-06-21 | 12:46 | **15:32** | +2 h 46 min |
| 2026-09-23 | 12:37 | 12:52 | +15 min |
| 2026-12-21 | 12:42 | 12:42 | 0 |
| 2026-03-21 | 12:52 | 13:12 | +20 min |

好消息：`ev["noon"]` 目前没有被任何界面用到（全仓 grep 只有定义与赋值），
所以现在只是埋了一颗雷。要么删掉，要么改成解析法求中天
（`12h − EoT − 经度修正`）或对高度角做三分搜索。

### P2-6　手输经纬度不认夏令时

```python
# app.py:414-418
offset = int(round(lon / 15.0))
tz = f"Etc/GMT{'-' if offset >= 0 else '+'}{abs(offset)}"
```

`Etc/GMT±N` 是**固定偏移**，没有 DST。用经纬度手输柏林（13.4, 52.5）会得到
`Etc/GMT-1`，而夏季真实是 UTC+2 → 整扇窗差一小时，天色错位会非常明显。
建议：手输坐标也走一次 geocode 拿正确的 IANA 时区；
离线时退化成「按经度取整 + 明确提示可能有一小时误差」。

### P2-7　零自动化测试，CI 只打包

全仓没有 `tests/`、没有 `pytest`/`unittest`、没有 lint 或类型检查；
唯一的回归网是 `AGENTS.md §7` 那份**手工清单**。CI（`.github/workflows/release.yml`）
只做「打包 → 校验版本号与包不空 → 挂 Release」，**代码本身一行都没被执行过**。

性价比最高的补法很具体（几秒钟跑完）：

1. `astronomy.py` 纯函数：与已知值对照 `sunrise/sunset/moonrise/moonset`（±1 min）、
   二分校验 `solar_noon`、月亮视差修正的回归；
2. `scene.ribbon()` 长度与端点、`weather.cloud_at()` 插值端点、`parse_version()` 边界
   （`v1.2.3` / `1.2` / `1.2.3-beta.1`）；
3. `wallpaper` 的槽位逻辑（`shown_slot` / `stale_shown_slot` / `is_our_uri`）——纯字符串，
   配一个假 `gsettings` 就能测；
4. 冒烟：`tools/snapshot.py` 出图不抛异常（现在 0.34 s，CI 完全吃得消）。

上面 P1-1、P1-2、P1-3 三个问题，各会有一条测试立刻变红。

### P2-8　文档仍在描述已经被证伪的壁纸机制

`AGENTS.md §3.5` 明确写了「以前那条『必须在 a/b 之间交替写』是错的」，
但仓库里至少四处还留着旧说法：

| 位置 | 现在的说法 | 与实现的关系 |
|---|---|---|
| `wallpaper.py:1-7` 模块 docstring | 「GNOME 对同一个文件路径未必会重新加载，所以在两个文件名之间来回写」 | **与实现相反**，实现是常态就地更新 |
| `DESIGN.md §4.1` | 「壁纸是靠往两个文件交替写、再改 URI 来触发的」 | 同上 |
| `README.md` 代价段 | 「外加每次 16 KB 的系统设置写入」 | 稳态已经不再写 `gsettings` |
| `AGENTS.md §7` 验收清单 | 「壁纸文件按 10 秒节奏在 sky-a/b 之间切换」 | 与它自己的 §3.5 自相矛盾 |

这个项目把「诚实」当卖点，文档漂移的代价比一般项目更高；更危险的是下一个人很可能
**照着 docstring 去改代码**，把 1.1.4 踩过的坑再踩一遍。
建议在 `CONTRIBUTING.md` 里加一条约定：机制类结论只允许写在 `DESIGN.md` 一处，代码里引用它。

---

## 4. P3 与小瑕疵

- **P3-1 动态壁纸失败会留一张空桌面**：`render_day()` 先把 `frames/frame-*.png`
  全部删掉再逐帧生成（`wallpaper.py:380-386`）。生成期间若进程退出或抛异常，
  已经在用的 `sky-day.xml` 还指着那些被删掉的文件。建议先生成到 `frames.tmp/`，
  最后整体 `os.replace` 目录再写 XML（原子换），或至少先把 XML 换成一个纯色兜底。
- **P3-2 重复定义**：`wallpaper.py:148` 与 `wallpaper.py:416` 各有一个 `set_wallpaper`，
  后者覆盖前者（实现相同，行为无差），但这是纯噪音，还会让人以为 `Worker` 走的是另一条路径。
- **P3-3 `app.py` 的上帝类**：`ChuangWindow` 同时是窗口、菜单/动作注册表、场景缓存、
  壁纸调度器、更新器 UI、安装器轮询器、诊断器、托盘桥。可以按已经天然存在的边界拆：
  `actions.py`（菜单 + 动作）、`wallpaper_ctl.py`（`apply_wallpaper` 一族）、
  `update_ui.py`（下载/安装/重启）、`diagnostics.py`（两份 `_diagnostics*`）、
  `dialogs.py`（5 个自绘窗口）。纯机械拆分，风险低，收益是可测性。
  下面那几层（`scene` / `palette` / `astronomy` / `city` / `street`）本来就是干净的，
  别让 app 层继续长。
- **P3-4 多显示器**：`screen_size()` 只取 `monitors.get_item(0)`，而 GNOME 对所有显示器
  用同一张图 + `zoom`。双屏用户会看到窗外比例被拉歪。至少可以按所有显示器的包围盒取尺寸，
  或在 README 里明确写「多屏时按主屏比例，其它屏会裁切」。
- **P3-5 无 i18n**：菜单、toast、信息卡、Release 说明全中文硬编码，
  `CONTRIBUTING.md` 也把「文案用中文」写成了约定。作为面向 GitHub 的开源桌面应用，
  gettext 化（哪怕先只做 `en` 一份）能直接扩大用户面，也是「欢迎贡献」的实际动作。
  建议先把**面向用户的字符串**抽到一处 `_()`，不要现在追求完整翻译。
- **P3-6 可访问性**：整个画面（含信息卡、长卷、提示条）都是 Cairo 位图，
  屏幕阅读器只能读到标题栏和菜单；键盘快捷键里只有空格在菜单里有提示，
  `Esc` / `Home` / `←→` / `F11` 只写在 README 里。至少把快捷键写进菜单项标签，
  并给画面加一条可读的替代文本（把 `human_hint()` 那句人话塞进
  `Gtk.AccessibleProperty.DESCRIPTION`），几乎零成本。
- **P3-7 首启引导**：`first_run_tips()` 只弹一条 7 秒 toast 说「按你所在时区推测为『西安』」
  （`app.py:1770-1782`），而 `DESIGN.md §3.1` 承诺的是「首次需要确认城市」。
  建议首启直接开 `CityDialog`（`chuang --city` 这条路径现成），
  或至少在信息卡顶部放一行「不是这里？换一扇窗」。
- **P3-8 性能口径**：实测（缓存已热，含信息卡与长卷）——960×620 晴天 8.6 ms/帧
  （10 fps ≈ 8.6% 单核，与 README 的「约 10%」相符）；1920×1080 晴天 15.9 ms/帧；
  1920×1080 中雨 22.9 ms/帧，而雨天帧间隔被压到 0.07 s → 约 1/3 个核。
  冷启动一帧 107 ms（切城市/切天气时会有一次肉眼可见的顿挫）。建议 README 给数字
  标上分辨率前提。
- **P3-9 `moon_phase()` 的返回契约**：docstring 说返回「亮面在天空中的方向」，
  实际第三个值是 `elong * RAD`（距离角），三个调用点全部丢弃它（`scene.py:207`）；
  真正做这件事的是 `bright_limb_vector()`。建议删掉或改名，并在 docstring 里指过去。
- **P3-10 配置没有版本号**：`Config` 只靠 `sanitize()` 纠错（`config.py:96-130`），
  历史字段（如已删除的 `desktop_anim`）只能悄悄消失。建议在 JSON 里加 `"schema": 1`，
  读取时按版本升级。
- **P3-11 零碎**：未使用的 import（`wallpaper.py:15` 的 `sys`、`weather.py:15` 的
  `timedelta`）；`chuang-gui` 没有 `--help`，未知参数被静默忽略（`--citty` 会被当成
  正常启动）；`weather.code_at()` 是 O(n) 扫描（48 点，无所谓，顺手二分即可）。
- **P3-12 DST 日的长卷与动态壁纸**：长卷按「本地 00:00→24:00，每 5 分钟一格」生成
  （`scene.py:249+`），动态壁纸按「一天 / 96 帧」切（`wallpaper.py:271-290`）。
  夏令时切换的那两天，这两条时间轴并不等于 24 个真实小时，欧洲/北美用户会看到
  长卷与真实时间错开一小时。当前主要用户在中国，影响面小，列此备查。

---

## 5. 架构评估

**分层清晰，依赖方向也对。**

```
astronomy / palette（纯计算，零 UI 依赖）
        ↓
weather（网络 + 缓存） → scene（组装一帧，纯数据）
        ↓
city / street / render（纯绘制，只读 Scene）
        ↓
wallpaper（离屏复用同一个 render） · tray（纯 Gio D-Bus） · app（GTK 生命周期与交互）
```

做得对的地方：`scene.py` 的「纯数据」纪律守得很好（`Scene` 是 dataclass，没有回调）；
`render.py` 不反向依赖 `app.py`；`wallpaper.Worker` 用组合复用同一个 `SkyPainter`
而不是复制一份绘制逻辑；`tray.py` 把 `Gio.Menu` 翻译成 dbusmenu 而不是手抄第二份菜单——
这三处都是「一个事实只有一处定义」的正确做法。调试口子（`CHUANG_TIME` /
`CHUANG_WEATHER` / `tools/snapshot.py`）让这个纯图形项目可测，这是很多同类项目缺的。

**三个结构性问题：**

1. **`app.py` 承担 8 种职责**（见 P3-3）。它不是「界面文件」，而是「界面 + 壁纸控制器 +
   更新安装器 + 诊断器」。后果是：每个新功能都往同一个类里加，`_tick_body` 里混着
   「重绘 / 壁纸兜底 / 标题刷新」三件事；P1-2 这类小 bug 也更难被发现
   （`_tray_activate` 的签名没人盯着）。
2. **缓存是「手工拼出来的 key」，而不是「带失效语义的对象」**。现在有 6 个离屏缓存
   （`_sky` / `_cloud` / `_overlay` / `_city` / `_sill` + `ui.ribbon_surface`），
   每个都自己拼 key，正确性依赖「作者记得把新参数写进 key」。P2-2 就是这条路的必然产物。
   建议引入一个三十行的 `LazySurface(key, draw)` 小工具，把「生成 / 复用 / 失效」收敛到一处。
3. **错误处理策略不统一**：有的地方 `except Exception: pass`（`config.save`、`_deliver`、
   `tray.stop`），有的地方写进诊断（`_tick`），有的地方弹详情窗（安装失败）。
   建议立一条线：**凡是影响用户可见行为的失败，都必须能进「诊断」文本**，其余才允许吞。
   现在「壁纸没换成」有诊断，「配置存不下」没有。

**线程模型也建议写进 `DESIGN.md`**：目前有 5 类后台线程（天气、壁纸单帧、壁纸整天、
geocode、更新检查/下载），全部 `daemon=True` + `GLib.idle_add` 回主线程，没有取消机制。
当前规模下没问题，但 `Worker.render_now` 与 `WeatherService` 的 `_busy` 都是在线程里
直接改的普通布尔（只有渲染本身受锁保护），以后要加「渲染中途换城市/换尺寸」就得当心。

---

## 6. 产品设计评估

**定位与克制是这份产品最大的优势**，先说清楚：一个动词（看）、零输入、不做七天预报 /
闹钟 / 星座连线——`DESIGN.md §4` 那张「刻意不做」清单比不少商业产品的 PRD 都清楚。
下面只讲会实际影响留存与口碑的点。

1. **「诚实」是卖点，就不能有 1° 级的月亮偏差**（P1-3）。太阳、星星、影子、云都经得起
   推敲，唯独月亮会被人抬头验证，而这恰好是最容易让人失望的一处。
2. **开关必须可信**（P1-1、P1-2、P2-4）。三个都是「用户表达了意图，产品没照做」。
   这类失信比缺功能更伤，因为它让人开始怀疑其它开关（比如「开机自启」到底写没写进去）。
3. **「放到桌面上」这条主场景门槛偏高**：用户需要理解「跟随此刻 vs 离线动态壁纸」、
   需要知道「必须留在托盘里才生效」、还要接受 10 秒一次的重绘。建议把菜单里的
   `桌面壁纸 ▸` 改成一次性引导（三个卡片式选项 + 一句话代价），把已经做得很好的
   「壁纸诊断」当作失败后的兜底，而不是用户的第一步。
4. **信息密度与「无字之美」有张力**：`DESIGN.md §5` 说「其余时间画面里没有一个字」，
   但默认 `show_info=True`，首屏是时间 + 6 行事实 + 一句人话。
   可以考虑「首屏只有景色，鼠标移入或按空格才出字」，并在首启示范一次。
5. **缺少「今天值得看一眼」的时刻提示**：金色时刻、月出、今天日落现在都藏在信息卡的
   静态行里。一个极轻的可选项（金色时刻前 20 分钟在窗框上亮一条细光，或托盘 tooltip
   写「距日落 25 分钟」）能显著提高「被瞄一眼」的频率——而这正是 `DESIGN.md §1`
   定义的场景。注意别做成系统通知，那会破坏克制原则。
6. **可发现性依赖外部文档**：拖动长卷、滚轮微调、空格、`←→` 都只写在 README 里；
   首启只有一句 toast。建议把 2–3 个手势做成一次性演示。
7. **城市选错后果是全盘错**（P3-7）：菜单第一组第一项确实是「换一扇窗」，够用；
   但首启没有确认步骤时，用户可能长期看着一座不是自己所在的城市而不知道能改。
8. **开源友好度**：无 i18n、无测试、无 lint、`CONTRIBUTING.md` 只有手工清单
   （P2-7、P3-5）。想被别人接手，最小改动是三件：`tests/`、一个 lint 任务、i18n 骨架。

---

## 7. 做得好的地方（重构时请不要弄丢）

- **纯标准库 + 系统包的坚持**：没有 `requirements.txt`、没有构建步骤，
  `.deb` 里就是可读的 Python。这是真实差异点，不是口号。
- **天文与绘制的分离**：`scene.py` 输出纯数据，`render.py` 只读不写；
  `tools/snapshot.py` 因此能在无窗口的情况下出图（本次审阅就是走这条路径验证的）。
- **离线优先**：断网时天空依旧完全正确，天气退化成「不知道有没有云」并明确标注。
- **诊断意识**：`_diagnostics_wallpaper()` 把「谁持有锁、桌面挂着哪张、心跳兜了几次」
  一次摊开，还配了可复制的详情窗——桌面应用里很少见。
- **托盘属性按扩展的实现（而不是按规范）对齐**：`WindowId` 用 `i`、`IconName` 故意留空、
  补齐扩展会问的属性，这是真踩过坑才写得出来的代码，而且救过用户所有托盘图标。
- **发版自动化 + 版本号单一来源 + CHANGELOG 驱动 Release 说明**：`1.1.7` 那套
  `plan → build → publish`（含「版本号没涨就不发版」）值得保留。
- **`_tick` 整体 try 包裹**那条注释（「定时器被异常打死」）非常有价值：
  它把一个极难复现的现场问题钉成了可读的知识。

---

## 8. 建议的修复顺序

**第一批（建议作为 1.1.8，全是小时级改动）**

1. P1-2 托盘 `Activate` 签名兼容 + 文案对齐（约 10 分钟）。
2. P1-1 `WeatherService.effective`（或关闭时清空天气），三处调用点改用它（约 1 小时）。
3. P1-3 月亮地平视差修正 + 精度口径写进文档（约 1 小时）。
4. P2-4「未联网 / 你关掉了天气」文案分流（约 15 分钟）。
5. P2-8 文档一致性一次性对齐（wallpaper docstring、DESIGN §4.1、README、AGENTS §7）（约 1 小时）。
6. P3-1 动态壁纸目录原子换 + P3-2 删重复定义（约 35 分钟）。

**第二批（1.2.0，需要一点设计）**

7. P2-1 `picture-options` 一起记、一起还原，并把「接管/还原」语义写进 DESIGN。
8. P2-2 缓存键带上城市种子 + `SkyPainter.invalidate_location()`（顺势把缓存收敛成小工具类）。
9. P2-3 `.deb` 先校验 SHA256 再 `sudo`。
10. P2-7 建 `tests/`（天文回归、wallpaper 槽位逻辑、snapshot 冒烟），CI 加一个 `test` job。
11. P2-6 手输坐标的时区策略（geocode 反查，或明确提示误差）。

**第三批（结构性，别忘）**

12. P3-3 拆 `app.py`；P3-10 配置加 schema 版本；P3-5 i18n 骨架；P3-6 快捷键与可访问性；
    P3-4 多屏；P2-5 删掉或修好 `solar_noon`。

---

## 附录 A：验证用的命令与结果

```bash
# 1) 语法：全部通过
python3 -m py_compile chuang/*.py tools/*.py chuang-gui

# 2) 画面没崩（两帧离屏渲染，各约 0.34 s）
python3 tools/snapshot.py /tmp/t1.png 18:35 34.34 108.94
python3 tools/snapshot.py /tmp/t2.png 21:30 34.34 108.94 63 95 18 200

# 3) 天文数值对照（与暴力扫描真值比）
#    2026-09-23 西安：日出 06:29 / 日落 18:43（合理）
#    solar_noon() 与真值最多差 2 h 46 min（见 P2-5）

# 4) 关闭天气后画面里还有没有天气（P1-1，用临时缓存，未动用户数据）
#    → 仍然有：云量 95.0，天气码 63（中雨）

# 5) 托盘 Activate 路径（P1-2）
#    → TypeError: _tray_activate() missing 1 required positional argument: 'name'

# 6) 城市缓存复用（P2-2）
#    → same coords, different city name -> cached surface reused: True

# 7) 月亮视差（P1-3）
#    → 地心高度 +0.069° 时，实际可见天顶位置 −0.941°（差 1.009°）

# 8) 帧耗时（缓存已热，含信息卡与长卷）
#    960×620 晴 8.6 ms | 1920×1080 晴 15.9 ms | 1920×1080 中雨 22.9 ms | 冷启动一帧 107 ms

# 9) 菜单动作自查（AGENTS.md 里那段脚本）
#    用了没注册：无；注册了没在菜单里用：win.show（托盘专用，符合预期）
```

审阅期间只写入了两个临时文件（`/tmp/t1.png`、`/tmp/t2.png`，已删除）；
**没有改动 `~/.config/chuang`，没有动用户的壁纸，没有启动测试实例去抢单例锁。**

## 附录 B：本次未覆盖、建议由作者确认的点

- 视觉正确性最终只能靠人眼验收（月相亮面朝向、影子方向、云的层次），
  本次只做了数值层面的抽查，没有逐张比对截图。
- 托盘在实际 GNOME 会话里的行为：我验证的是扩展源码路径 + 处理器确实抛异常，
  建议在真机上双击一次托盘图标，并跑一遍 `AGENTS.md §7` 的日志检查确认。
- 自更新的真实安装链路（需要一次真实升级，会改动系统状态）没有执行。
- 多显示器、非 GNOME 桌面（KDE / MATE / Cinnamon 分支）、高 DPI 缩放下的表现未实测。
- `scene.build()` 在极端参数（极地、`fov` 边界、窗口 360×260）下的排版未逐个截图验证。
