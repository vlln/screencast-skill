# 光标素材（cursor assets）

光标是**后期合成**的：采集时 `-capture_cursor 0` / `-draw_mouse 0` 把系统光标排除，
输出里的指针由 `sclib/layers/cursor.py` 重画。所以样式完全可控，可以随便换。

## 三种来源

| 来源 | 命令 | 许可 |
|---|---|---|
| **内置矢量**（默认，9 种状态） | 无需操作 | 本项目自有，无限制 |
| **macOS 系统光标** | `rec cursors` | Apple 专有美术资源 —— **仅本地自用，不要随 skill 分发** |
| **任意 PNG 集** | `rec cursors --import <dir> --out <odir>` | 取决于来源，见下 |

## 格式：`meta.json`

```jsonc
{
  "version": 1,
  "cursors": {
    "arrow":  { "path": "assets/cursors/arrow.png", "hotspot": [0.0, 0.0] },
    "text":   { "path": "assets/cursors/text.png",  "hotspot": [0.5, 0.5] }
  }
}
```

- `path` 用**相对 skill 根目录**的路径，目录搬走不失效
- `hotspot` 是**比例**（0–1），不是像素 —— 任意渲染尺寸下热区都对
- 缺哪个 style 就回退到内置矢量，不会报错

然后在 theme 里指向它：

```jsonc
"cursor": { "size": 40, "dark": true, "assets": "assets/cursors/meta.json" }
```

## 样式键（9 种）

`arrow` `text` `pointer` `crosshair` `open-hand` `closed-hand`
`resize-ew` `resize-ns` `not-allowed`

和 Recordly 的 `cursorType` 集合一致，所以 plan 里记录的指针状态能直接映射。

## 导入一套 PNG

`rec cursors --import` 会递归找 PNG，按文件名**模糊匹配**到上面的样式键，
同一样式有多个尺寸时取最大的：

```
left_ptr.png / default.png      -> arrow
xterm.png / ibeam.png / text.png-> text
hand2.png / pointing_hand.png   -> pointer
cross.png / tcross.png          -> crosshair
hand1.png / open_hand.png       -> open-hand
fleur.png / grabbing.png        -> closed-hand
sb_h_double_arrow.png           -> resize-ew
sb_v_double_arrow.png           -> resize-ns
crossed_circle.png / no-drop.png-> not-allowed
```

```bash
rec cursors --import ~/Downloads/SomeTheme/cursors --out assets/cursors-mine
# 然后 theme: "cursor": { "assets": "assets/cursors-mine/meta.json" }
```

热区默认值按惯例给（箭头 = 左上角、其余 = 中心）。如果某张图明显偏了，直接改
`meta.json` 里的 `hotspot` 比例即可，不需要重新导入。

## 已知来源（**用前务必自己核对 LICENSE**）

| 项目 | 许可（GitHub API 实测） | 备注 |
|---|---|---|
| [ful1e5/Bibata_Cursor](https://github.com/ful1e5/Bibata_Cursor) | **GPL-3.0** | 4.2k ★，X11/Windows 构建，含多尺寸 PNG |
| [guillaumeboehm/Nordzy-cursors](https://github.com/guillaumeboehm/Nordzy-cursors) | **GPL-3.0** | Nord 配色系 |

> ⚠️ **GPL-3.0 的含义**：拿它渲染你自己的视频没问题；但把素材**打包进要分发的
> skill / 仓库**会让整个分发物受 GPL 约束。所以本 skill 默认只带自有矢量光标，
> 第三方素材请由使用者**本地导入**，不要提交进仓库。
>
> 想找可再分发的素材，优先找 **CC0 / MIT / 无版权声明** 的包；找不到就用内置矢量，
> 或者自己画（`sclib/layers/cursor.py` 里每个形状就十几行）。

## Agent 取素材的推荐流程

1. 先问清用户要不要换光标、偏好什么风格；
2. 优先用 `rec cursors`（本机、零下载、7 种可用）；
3. 需要特定风格时再去上面列的项目找，**先读 LICENSE**，确认可分发再放进 skill 目录；
4. 下载后一律走 `rec cursors --import`，不要手动拼 `meta.json`；
5. 渲染一帧用 `rec sheet` 看热区对不对（箭头尖是不是指在正确位置）。
