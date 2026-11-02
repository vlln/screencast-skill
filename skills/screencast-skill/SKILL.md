---
name: screencast-skill
description: 当用户要录屏、要做产品演示视频、要把操作过程做成精致短片，或要给 Agent 自己的操作留下可展示的录像时使用本 skill。在 macOS 上录制屏幕，再在后期重绘成有"手录感"的演示视频——平滑且可换样式的光标、点击涟漪、键帽、按节拍推近的镜头、卡片背景。捕获只记事实（raw.mov / events.jsonl / meta.json），决策写进可改的 plan.json，渲染只做执行，所以同一条素材能反复重出。判定：需要的是"录下来再看"就用它；需要实时直播或系统级录屏服务，不用。
license: MIT
metadata:
  author: vlln
  version: "2.0.0"
  platform: macOS 26+
  python: ">=3.10"
---

# screencast-skill

> **两个词先说清楚**
> - **`rec`** — 这套工具的命令行入口，就是 `scripts/rec` 这一个文件。录制、渲染、体检都通过它。
> - **一次录制（代码里叫 session）** — 一次 `start` 到 `stop` 产出的**一个目录**，里面固定三个文件：
>   `raw.mov`（素材）、`events.jsonl`（事件）、`meta.json`（尺寸与时钟）。之后所有渲染都读这个目录。

macOS 录屏 + 后期合成特效。**捕获是笨的，渲染是聪明的。**

```
facts    raw.mov + events.jsonl + meta.json   录制，不可变
plan     plan.json                            决策，可读可改        <- references/PROTOCOL.md
render   theme.json + layers -> frames        执行，可丢弃重来
```

效果发生在录制之后，所以同一条素材可以反复重渲染，也不需要向被拍摄的机器注入特效。

## 安装（一条命令，无需 Xcode）

**先查，再装。** 新会话不记得上一次装过什么，所以不要盲装：

```bash
scripts/install.sh --check        # 已装 → "already installed"，退出码 0
```

退出码 0 就直接往下走，什么也别装；退出码 1 才跑安装：

```bash
scripts/install.sh
scripts/.venv/bin/rec doctor      # 顺带检查权限，缺什么它会说
```

`install.sh` 本身也是幂等的：`.venv` 已存在且能 import 依赖时它直接打印
`already installed` 并退出，不碰网络。所以即便忘了先 `--check`，重复执行也不会重装。

用 `uv` 在 `scripts/` 下建 `.venv`（Python **3.12**）并装三个 wheel：`pyobjc-framework-Quartz`、`numpy`、`Pillow`。
实测约 **12 秒 / 71MB**。外部依赖只有 `ffmpeg`。

> ⚠️ **必须 Python ≥ 3.10。** 系统自带 3.9 没有 pyobjc wheel，`pip install` 会尝试源码编译并失败。

## 权限（两个，缺一不可）

1. **屏幕录制** — 隐私与安全性 > 屏幕录制
2. **辅助功能** — 隐私与安全性 > 辅助功能（否则拿不到鼠标/键盘事件）

`rec doctor` 逐项检查并打印修法。

## 用法

```bash
R=scripts/.venv/bin/python

scripts/.venv/bin/rec doctor                       # 先跑这个
scripts/.venv/bin/rec windows                      # 列出可录窗口

scripts/.venv/bin/rec run --duration 10                        # 全屏
scripts/.venv/bin/rec run --duration 10 --window front         # 前台窗口
scripts/.venv/bin/rec run --window <id> --script demo.txt      # 边录边自动演示
scripts/.venv/bin/rec run --duration 10 --audio mic            # 带麦克风
scripts/.venv/bin/rec run --duration 10 --audio system         # 带系统声音（需 BlackHole）
scripts/.venv/bin/rec run --duration 10 --countdown 0          # 不要倒计时（默认 3 秒）
scripts/.venv/bin/rec run --duration 10 --quiet                # 倒计时和结束提示都关掉（适合无人值守）

scripts/.venv/bin/rec plan captures/<session>                  # 生成/重算 plan.json
scripts/.venv/bin/rec render captures/<session>                # -> out.mp4
scripts/.venv/bin/rec render captures/<session> --pad 150 --radius 26 --bg "#0b1220,#203e5a"
scripts/.venv/bin/rec sheet captures/<session>/out.mp4 --times 0.5,2,4
```

`rec run` 必须把启动、驱动、停止放在**一条命令**里。分多次调用会把操作者/模型的延迟
漏进素材，动作会落到视频末尾。

### 驱动脚本（Agent 自己演示时）

```
expect TextEdit      # 前台不是这个 App 就整段中止（安全阀，别省）
save
move 708 404 800
click
type Hello screencast
key cmd+a
restore
```

`expect` 不是装饰：注入事件发给**当前前台 App**，机器正在被使用时没有它会误点。

**演示样式切换时，先把指针停住。** 想让人看清"光标样式变了"，指针就得待在原地不动——
一边移动一边换样式，观众只看到一团在动的东西，看不出换了什么。脚本里写成
`move <x> <y> <ms>`，然后 `sleep` 足够长，样式变化交给 `theme.timeline` 去做：

```
move 1500 520 700     # 移到位
sleep 3400            # 停住，这段时间里 timeline 依次换 force_style
```

### 录制前后的两个提示（给人看的，给 Agent 看的是 JSON）

这两件事只对人有用，所以它们是这套工具里唯一的两个 GUI：

| 时机 | 样子 | 意义 |
|---|---|---|
| 开录前 | 屏幕中央的液态玻璃圆盘，环形进度 + `3·2·1`，每秒一声轻响 | 给了把人坐好、鼠标摆好的时间；**倒计时不在成片里** |
| 停止后 | 顶部一条玻璃胶囊：`✓ 12.3s · 8.4 MB` + 录制目录名，2.4s 淡出 | 确认"真的停了" |

```bash
--countdown N     # 秒，默认 3；0 = 关
--quiet           # 全部关掉（也可用 REC_QUIET=1）
```

#### 视觉：跟着系统的 Liquid Glass 走

用 `NSGlassEffectView`（macOS 26+ 的系统组件），而不是自己画一块"玻璃"：

- **`.regular` 变体**：自适应、最易读。它自己会模糊背后内容、随背景亮度和系统外观调整，
  所以**内容色一律用语义色**（`NSColor.labelColor` / `secondaryLabelColor`），
  绝不写死白色或黑色——写死会在另一种外观下失效，这正是 HIG 明确反对的
  （[Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)）。
- **形状只有两种**：倒计时用圆盘（径向进度），提示用胶囊。胶囊是 HIG 里控件的基本形。
- **不自己画阴影、不画描边、不铺压暗层**。玻璃自带边缘高光与透镜带；再叠一层"内阴影"
  会变成"框中框"，而手画的投影在圆角外会露出一块灰色，比没有还难看——
  这块多余的阴影之前就实实在在地糊在右上角。
- **不强制深色外观**。之前为了让白字可读，强行把窗口设成 DarkAqua 再铺黑色压暗层，
  结果就是"老版深色 HUD"的样子。现在交给系统：外观由用户设置决定，玻璃自己适配。

> 兜底：没有 `NSGlassEffectView` 的旧系统退回 `NSVisualEffectView`（HUD 材质），
> 同样的排版，仍旧是真模糊而不是假透明。

#### 三条实现上的硬要求（改这块时别破坏）

1. **不能抢焦点**。用 `NSApplicationActivationPolicyAccessory` + `orderFrontRegardless`，
   它浮在最上层但**不激活 App**——否则 `expect` 会立刻失败，录出来的也不是原来那个窗口。
2. **不能吃点击**。窗口 `ignoresMouseEvents`，否则会吞掉本该点到下面窗口的那一下。
3. **必须先消失再开录**。`orderOut` 是往 window server 的一次往返，进程直接 `os._exit`
   会把最后一帧留在屏幕上——它就会成为这次录制的**第一帧**。所以是
   "先 orderOut → 让 run loop 再转 0.3 秒 → 才退出"，`rec` 再等 0.18 秒才启动 ffmpeg。

## 工作目录

**数据不放在 skill 目录里**——装好的 skill 可能只读、多人共用、或在版本控制下。

数据根目录**按需解析**，取第一个可写的：

| 优先级 | 位置 | 用途 |
|---|---|---|
| 1 | `$REC_HOME` | 显式覆盖（换机器 / 换项目 / 放大盘） |
| 2 | `~/Movies/screencast-skill` | macOS 常规位置 |
| 3 | `./screencast-skill-data` | 前两个都不可写时，落在 agent 自己的工作目录 |

`rec doctor` 会打印实际用了哪个以及来源标记（`[REC_HOME]` / `[default]` / `[cwd]`）。
`REC_HOME` 指到不可写的位置不会硬失败，会警告并降级到下一级。

```
$REC_HOME/                                  # 解析结果，默认 ~/Movies/screencast-skill
└── captures/
    └── 20261102-010211-window/             # 一次录制 = 一个目录
        ├── raw.mov                         # 事实：原始捕获（光标已排除）
        ├── events.jsonl                    # 事实：输入 + 窗口焦点时间线
        ├── meta.json                       # 事实：几何 / fps / 时钟对齐
        ├── plan.json                       # 决策：可读可改
        ├── out.mp4                         # 产物：默认渲染
        └── sheets/                         # 产物：抽帧验证图
            └── out.png
```

- 换位置就设 `REC_HOME`：`REC_HOME=/Volumes/SSD/rec`（不是写死的常量）
- `rec run --out DIR` 可以只对这一次指定会话目录
- `rec list [--json]` 列出所有录制（时长/大小/是否已渲染），agent 用它找素材
- `rec doctor` 会打印当前 data home 并检查是否可写

> 旧版本把会话写在 skill 自己的 `captures/` 下，已废弃。迁移：
> `mv captures/* "$REC_HOME/captures/"`

## plan：可编辑的决策层

### 聚焦的四个可调点

| 参数 | 默认 | 管什么 |
|---|---|---|
| `zoom` | 1.25 | 放大倍数。1.45 在完整界面里会显得过冲 |
| `pad_pre` | 0.25 | 点击**前**多久开始推近（太多会"提前动"） |
| `pad_post` | 1.6 | 交互**后**停留多久（太少看不到结果） |
| `min_duration` | 2.0 | 区域最短时长。1 秒的区域全是加减速、镜头根本停不住，这是"别扭"的主因 |
| `center_bias` | 0.25 | 焦点从点击点向画面中心靠多少，避免贴着边缘取景丢掉上下文 |

### 手写相机区间时，`follow` 必须关掉

`plan.camera` 里一个区间的 `center` **默认只是初始焦点**——区间开着 `follow` 时，相机会
从头到尾追着光标跑，你写的 `center` 一帧之后就被覆盖了。这就是"手写了居中，渲染出来却被
裁到画面外"的原因。要让它literally生效：

```jsonc
{"t0": 4.3, "t1": 7.7, "zoom": 1.44, "center": [1180, 430], "follow": false}
```

`center` 用的是**屏幕坐标**（和 `clicks` 同一个空间），不是窗口内坐标。可以这样先验一把：

```python
from sclib.render import camera_path
zs, xs, ys = camera_path(regions, n, fps, base_rect, follow=False)
# xs[i] 是裁切左边界，xs[i] + bw/zs[i] 是右边界
```

### 相机跟随（Recordly 行为）

放大期间相机**不是死盯点击点**，而是保持一个中心、只在光标越出内圈安全区时才移动：

- `follow_cursor`（默认 `true`）— 关掉就退化成"固定盯区域中心"
- `safe_zone`（默认 `0.35`）— 内圈安全区占可视范围的比例，越大越"懒"
- `follow_omega`（默认 `5.0`）— 移动的弹簧刚度，越小越柔
- `min_move_interval`（默认 `0.30`）— 重新定位的最小间隔；不加节流会逐帧追光标

越界后焦点只做**最小幅度**移动（把光标顶回安全区边缘），不是跳到光标——这是避免
"镜头永远在飘"的关键。想钉死机位就在 plan 的区域里写 `"follow": false`。

缩放回落时焦点会**冻结**，镜头保持位置直到完全拉回，避免画面漂移。
实测量化：受控轨迹下，光标越界前相机静止、越界后行程 1254px（关闭跟随时仅 640px 的弹簧余量）。

`plan.json` 记录"什么时刻、哪个位置、几倍聚焦、什么指针样式"，格式见
[`references/PROTOCOL.md`](references/PROTOCOL.md)——读它的时候：要手改 `plan.json`、
要给某个区间钉死镜头、或要确认某个字段的含义。要点：

- `camera` 是**目标台阶**不是关键帧；缓动是渲染层的执行细节（临界阻尼弹簧 + 钳制），
  所以手改一个 zoom 值仍然是平滑的。
- `cursor.styles` 是指针状态时间线，支持 `arrow / text / pointer / crosshair /
  open-hand / closed-hand / resize-ew / resize-ns / not-allowed`。
- 自动算法对齐 Recordly 实测参数：点击聚类合并 **2.5s**、区域两端各 pad **0.5s**、
  **只用显式点击**驱动自动缩放。

```bash
scripts/.venv/bin/rec plan <session>                  # 重算
$EDITOR <session>/plan.json            # 改一个 zoom / 挪一个区域
scripts/.venv/bin/rec render <session>                # 按你的改动执行
scripts/.venv/bin/rec render <session> --rebuild-plan # 丢弃改动重新推导
```

## theme：可替换的样式

`scripts/themes/default.json` 分四块，与图层一一对应，互不影响：

```jsonc
{
  "background": { "type": "gradient|solid|image", "top": "...", "bottom": "...",
                  "image": "wallpapers/x.png", "pad": 90, "radius": 20, "shadow": true },
  "camera":     { "zoom": 1.25, "smoothing": 22.0,
                  "follow_cursor": true, "safe_zone": 0.35, "follow_omega": 5.0,
                  "pad_pre": 0.25, "pad_post": 1.6, "min_duration": 2.0,
                  "center_bias": 0.25 },
  "cursor":     { "size": 40, "dark": true,
                  "auto_scale": { "gain": 0.34, "speed_ref": 900,
                                  "click_pulse": 0.11, "smoothing": 10.0 } },
  "keycap":     { "mode": "line", "view": "text",
                  "position": "bottom-center", "max_visible": 5, "hold": 1.15,
                  "size": 0.045, "gap": 0.35, "radius": 0.30,
                  "fill": [...], "border": [...], "text": [...], "margin": 0.10 },
  "ripple":     { "duration": 0.62, "radius_from": 14, "radius_to": 66,
                  "thickness": 3, "dark": [...], "light": [...],
                  "press_scale": 0.88, "press_time": 0.18 }
}
```

每个视觉元素是 `sclib/layers/` 里的独立模块（`background.py` / `cursor.py` /
`keycap.py` / `ripple.py`），互不知道对方存在。要换观感：改 theme；要换画法：换那一个 layer。

### 光标美术资源（可替换）

光标和键帽一样是**后期合成**的（采集时排除系统光标），所以样式完全可控。
两种来源：

1. **内置矢量**（默认）——13 种状态：`arrow / text / pointer / crosshair /
   open-hand / closed-hand / resize-ew / resize-ns / move / cell / zoom-in /
   zoom-out / not-allowed`
2. **图片资源**——`rec cursors` 导出 macOS 真实系统光标到 `assets/cursors/`，
   然后 theme 里指定：

```jsonc
"cursor": {
  "size": 40, "dark": true,
  "assets": "assets/cursors/meta.json"
}
```

**先看再选**：`rec styles` 出一张对照图（所有光标形状 + 所有键帽预设），

```bash
rec styles --out /tmp/styles.png
```

### 指针形状：录下来，不是猜出来的

指针该是箭头、I-beam 还是手，取决于**指针下面是什么元素**。录制时用
`AXUIElementCopyElementAtPosition` 每 120ms 采样一次，按元素角色记一条事件：

```json
{"t": 1.83, "type": "cursor", "shape": "text", "role": "AXTextArea"}
```

角色映射：`AXTextField/AXTextArea/AXSearchField/AXComboBox` → `text`；
`AXButton/AXLink/AXMenuItem/AXCheckBox/AXRadioButton/AXPopUpButton` → `pointer`；其余 → `arrow`。

`plan.build_cursor_styles` 优先用这些**实测**段落（`reason` 写成 `ax:AXTextArea`），
只有在没有采样数据时才退回按键击行为猜的旧启发式。所以鼠标移到正文上会变成 I-beam、
移到按钮上会变成手——不需要主题里写任何规则。

代价：多一次 AX 查询（约 1ms/次），且需要辅助功能权限；没有权限时这一步静默跳过，
鼠标退回全片一个箭头。

### 光标自动缩放（Recordly 式）

指针不是固定大小的贴图：**手慢下来瞄准时变大，快速移动时变小，点击时弹一下**。
这是 `cursor_scales()` 从录到的轨迹里推出来的——只用到速度，而且速度已经被弹簧平滑过，
所以是"呼吸"而不是"抽搐"。关掉它：

```jsonc
"cursor": { "auto_scale": { "gain": 0 } }
```

`gain` 是停住时最多放大多少（0.34 = 大 34%），`speed_ref` 是"算在移动"的 px/s，
`click_pulse` 是点击瞬时增量。

### 键帽：四种画法，不是四种配色

键帽的样式是**画法**，颜色只是画法里的一个字段。移植自 Keyviz 的四个组件：

| 样式 | 画法 | 一眼可辨的特征 |
|---|---|---|
| `minimal` | 没有键体 | 只有字形，最轻 |
| `laptop` | 单层键面 | 顶部受光渐变 + 顶边内嵌高光 + 底下一点硬投影 |
| `lowprofile` | 键面 + 底座 | 底座在下沿露出一条边 |
| `pbt` | 厚底座 + 内缩键面 | 深色底座包着浅色键面，键面底部一条细边 |
| `panel` | 不画键，只画一条半透明条 | 等宽字体把键名横排（`SPACE` / `RETURN`），整行居中。移植自 ShowMeTheKey |

尺寸照抄 Keyviz：键体高 2.25–2.75 倍字号，修饰键宽 2.5 倍、普通键 2.25 倍，
圆角是 0–1 系数乘 1.25 倍字号。`rec styles` 里每一行都会用**同一个画法画两遍**
（深色 / 浅色底各一遍），就是为了说明：换色不等于换风格。

```bash
rec render <录制目录> --keycap-preset lowprofile   # minimal / laptop / lowprofile / pbt / panel
```

**位置、大小、透明度都是主题字段**，不是常量：

```jsonc
"keycap": {
  "position": "top-center",   // bottom|top × left|center|right，六种
  "offset_x": 0.0,            // 相对帧高的微调（±），用来躲开视线焦点
  "offset_y": 0.02,
  "size": 0.082,              // 字号占帧高的比例，键帽整体跟着缩放
  "opacity": 1.0,             // 0..1，整层统一压透明
  "margin": 0.10              // 距边缘
}
```

这几项都可以在 `theme.timeline` 里**中途改**。演示里就用了：
光标样式的那个节拍把字幕挪到 `top-center`，因为原来它正好压在指针上——
**看不见的演示等于没演示**。

**特殊键的写法**（这也是之前最丑的地方）：

| 键 | 画成 |
|---|---|
| 空格 | 一条**宽横杠** + 下面的 `space` 字样 |
| 回车 | `↩` + `return` |
| Tab | `⇆` + `tab` |
| 退格 | `⌫` + `del` |
| Esc | 直接写 `esc` |
| 方向键 | 只画箭头 |

空格绝不再是"一个小方块"——那个小方块是 U+2423 的字形，没有人看得懂，现在它是
一条画出来的横杠。另外**在句子里**（`line` 模式）空格就是空格，不会再画成方块：
方块只属于键帽视图。

`view` 控制显示物理键还是落字：中文走输入法时这两件事不一样，`key` 显示拼音字母，
`text` 显示汉字。

### 背景：可调参数与预设

卡片背景不是一个写死的渐变。参数对齐视频工具常见的那些（Recordly 有渐变/纯色/壁纸、
模糊、内边距、圆角、投影；这里再加多段渐变、角度、暗角和颗粒）：

```jsonc
"background": {
  "type": "gradient",              // gradient | solid | image
  "stops": [{"at": 0, "color": "#090f1c"}, {"at": 0.55, "color": "#152a45"},
            {"at": 1, "color": "#1f4b73"}],
  "angle": 118,                    // 度数，90 = 从上到下
  "pad": 92, "radius": 24,
  "vignette": 0.26,                // 压暗四角
  "grain": 0.028,                  // 胶片颗粒（固定种子，逐帧不跳动）
  "shadow": {"blur": 36, "dy": 22, "alpha": 165},
  "blur": 0, "dim": 0.0            // 仅 type=image：背景模糊 / 压暗
}
```

现成的一套在 `scripts/themes/backgrounds.json`：

```bash
rec render <录制目录> --bg-preset midnight    # midnight / obsidian / ocean / violet
                                          # sunset / graphite / paper / mint
```

`vignette` + `grain` 是这次让背景"看起来是设计过的"的关键两个参数——
没有它们，两色渐变永远像一张 PPT 背景。

## 音频

`--audio` 接受设备索引、名字子串，或 `mic` / `system` 两个语义值：

- `mic` — 自动找含 "mic" 的设备；桌面 Mac（Mac Studio / Mac mini）**没有内置麦克风**，
  没有时会给出明确报错而不是静默录出无声文件
- `system` — 找 BlackHole / loopback；需要 `brew install blackhole-2ch` 并在
  「音频 MIDI 设置」建多输出设备，把系统输出同时送到扬声器和 BlackHole

渲染时音频是**直通**（`-c:a aac` 从原始素材复用），不会因为加特效而丢声音。

## 跨平台

流水线本身是平台无关的：`sclib/plan.py`、`sclib/render.py`、`sclib/layers/*` 里
**没有任何 OS 调用**。平台差异全部收在 `sclib/backends/` 的八个函数里。

| 平台 | 后端 | 采集 | 输入 | 状态 |
|---|---|---|---|---|
| macOS | `backends/darwin.py` | avfoundation | pyobjc CGEventTap | ✅ 实测 |
| Windows | `backends/windows.py` | gdigrab | pynput | ⚠️ **未实测** |
| 其它 | `backends/unsupported.py` | — | — | 未实现 |

新增平台 = 写一个模块，提供：
`display_info / list_windows / audio_devices / resolve_audio /
capture_inputs / permissions / events_module / drive_module`。

### Windows 首次运行请逐项确认

这套 Windows 代码是在 macOS 上按 gdigrab / pynput 的文档写的，**没有在 Windows 上跑过**。
按这个顺序验最容易定位问题：

1. `rec doctor` 显示 `backend=windows, capture=gdigrab`
2. 录一段：画面不是黑屏，且**没有系统光标**（`-draw_mouse 0` 生效）
3. `rec windows` 的窗口标题与前台顺序对
4. 事件的 `x/y` 与画面像素一致 —— **DPI 缩放下最容易错**，必要时改
   `backends/windows.py` 的 `display_info`
5. `--audio mic` 能解析到 dshow 设备名

Windows 与 macOS 的已知差异：
- 没有 TCC 式授权，但**普通权限进程收不到以管理员运行的程序输入**，需提权
- 系统声音要开「立体声混音」或用 VB-Audio Virtual Cable
- 特殊键的 `code` 是 `-1`、键名放在 `text` 里（Windows VK 码与 macOS keycode 不同名）

## 关键约束与坑

- **窗口模式是"录全屏 + 裁窗口矩形"**，非原生窗口采集；小窗口会浪费捕获分辨率。
- **系统光标排除**靠 `avfoundation -capture_cursor 0`，已在 macOS 27 实测有效
  （在点击坐标处裁 200×200 是全白，没有系统箭头）。
- **`screencapture -v` 在新版 macOS 上可能完全不可用**（保存失败），所以走 ffmpeg。
- **修饰键符号只有 `SFNS.ttf` / `Apple Symbols.ttf` 有**；Helvetica/Arial 出豆腐块。
  而且**没有一个字体同时有中文和键帽符号**（PingFang 有汉字没 ␣⏎，SFNS 反过来），
  所以键帽是**逐字符选字体**画的，不是整行一个字体。␣ 是手画的（系统字形太小且贴基线）。
- **`CGEventCreateKeyboardEvent` 会继承当时的物理修饰键状态**，这是"整行字丢掉"的真正
  原因：用户随手按住 Fn/Command 的那一瞬间注入的按键会带上该修饰键，App 把它当快捷键
  而不是文本来处理（TextEdit 在 Fn 按下时直接丢弃 unicode-string 事件），而 keycode 0 的
  文本事件在 Command 下会变成 `cmd+a` / `cmd+s`。修复：注入前等修饰键松开，且**显式
  `CGEventSetFlags(ev, 0)`**。复现/验证方式见 `references/PROTOCOL.md`。
- **鼠标事件同样继承修饰键**，不清零会把点击变成 cmd-click。
- **中文输入法会截走注入的 ASCII**：输入源停在拼音时，注入的 `h`/`i` 被当成拼音组合，
  App 收到的是碎片甚至什么都没有（候选条就是证据）。`rec run --script` 现在会**自动**在
  驱动前后切换输入源（`sclib/imeswitch.py`，`--no-ime` 可关）。两个坑：
  ① TIS 的调用必须来自**有窗口服务器连接**的进程，纯 CLI 调 `TISSelectInputSource` 会永久挂住；
  ② 它返回的 CFArray 拥有元素，释放数组后再 `TISSelectInputSource(src)` 会挂住。
  所以它跑在**独立子进程里并带超时**，永远不可能卡住一次录制。
- **拖动必须发 `kCGEventLeftMouseDragged`**，按键按下期间发 `MouseMoved` 窗口服务器不收，
  表现为"点了没反应"。
- **Pillow 的图形基元没有抗锯齿**，任何形状都要超采样后 LANCZOS 降采样，否则圆角呈阶梯状。
- **ffmpeg 收到 SIGINT 后可能要约 10 秒才退出**，所以停止顺序是「先杀事件录制器，再
  SIGINT/SIGKILL ffmpeg」，否则事件录制器会多跑十几秒。
- 渲染吞吐：2560×1440 约 30–40 帧/秒；小窗口远快于此。

## 架构原则（改代码前先读）

- **facts → plan → render**。录制期不加特效；决策写进 plan.json；渲染只做执行。
- **图层独立**。光标 / 键帽 / 涟漪 / 背景各自独立，互不依赖。
- **样式是数据**。颜色、尺寸、时长走 theme，不写死在绘制代码里。
