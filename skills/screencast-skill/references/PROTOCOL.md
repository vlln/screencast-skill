# PROTOCOL v1 — facts → plan → render

Three artifacts. The split matters: **facts are recorded, decisions are written
down, pixels are disposable.** Re-render with a different theme or a hand-edited
plan and you never touch the recording.

```
$REC_HOME/captures/<session>/               # default $REC_HOME=~/Movies/screencast-skill
  raw.mov        facts   screen capture, OS cursor excluded
  events.jsonl   facts   input + window-focus timeline
  meta.json      facts   geometry, fps, clock alignment
  plan.json      plan    what the camera/pointer should do, and why   <- editable
  out.mp4        render  default output
  sheets/*.png   render  contact sheets for verification
```

One take = one directory. Nothing is written inside the skill directory.

The root is resolved per run, first writable wins:

1. `$REC_HOME`
2. `~/Movies/screencast-skill`
3. `./screencast-skill-data`   (agent's working directory)

`rec doctor` reports which one was chosen and why. A single session can be
redirected with `rec run --out DIR`; `rec list [--json]` enumerates takes.

---

## 1. facts

### `events.jsonl`
One JSON object per line, `t` = seconds since the recorder started.

| type | fields | meaning |
|---|---|---|
| `start` | `trusted`, `pin`, `pid`, `wall` | recorder start; `wall` is epoch time, used to align the video clock |
| `move` | `x`,`y` | pointer moved (60–200 Hz) |
| `down` | `x`,`y`,`btn`,`count` | button pressed; `count`>1 means double/triple click |
| `up` | `x`,`y`,`btn` | button released |
| `scroll` | `x`,`y`,`dx`,`dy` | wheel |
| `keydown` | `code`,`text`,`flags`,`repeat` | `text` is the real character, `flags` the modifier mask |
| `keyup` | `code`,`text`,`flags` | |
| `mod` | `flags`,`code` | modifier-only change (⌘⌥⌃⇧ never arrive as keydown) |
| `win` | `front`,`fx..fh`,`pin`,`px..ph` | window focus / geometry changed |
| `stop` | — | recorder stopped |

Coordinates are **global screen points** (top-left origin), the same space as
`CGEventGetLocation`. Convert to pixels with `(x - origin) * scale` from
`meta.display`.

Modifier bits: `⌃ 1<<18`, `⌥ 1<<19`, `⇧ 1<<17`, `⌘ 1<<20`.

### `meta.json`
`width`, `height`, `fps`, `duration`, `target` (`screen`|`window`), `window`,
`display{origin,points,pixels,scale}`, `sync_offset`.

`sync_offset` is the key to alignment:

```
video_time = event_time - sync_offset
```

It is `ffmpeg_first_frame_wallclock - recorder_start_wallclock`, measured from
the moment ffmpeg prints `Press [q] to stop`. Accuracy is about one frame.

---

## 2. plan (`plan.json`)

The decision layer. Produced by `sclib.plan` (algorithm) or by hand. The
renderer executes it without second-guessing.

```jsonc
{
  "version": 1,
  "source": { "video": "raw.mov", "width": 2560, "height": 1440, "fps": 30,
              "duration": 5.9, "display": {...}, "sync_offset": 0.5078 },

  "stage": {                      // the region the camera may move inside
    "target": "window",
    "window": 68584,
    "rects": [ { "t": 0.0, "rect": [338, 132, 673, 439] } ]
  },

  "camera": [                     // zoom regions; empty = no zoom at all
    { "t0": 1.549, "t1": 4.417, "zoom": 1.45,
      "center": [596.0, 310.7],
      "reason": "click-cluster", "clicks": 3 }
  ],

  "cursor": {
    "default_style": "arrow",
    "smoothing": 22.0,            // critically-damped spring omega
    "styles": [                   // pointer state over time
      { "t0": 3.817, "t1": 5.317, "style": "text", "reason": "double-click-like" }
    ]
  },

  "clicks": [ { "t": 2.049, "x": 708.0, "y": 404.0, "button": "left", "count": 1 } ],
  "keys":   [ { "t": 2.146, "label": "H", "key": "H", "text": "h",
                "kind": "char", "word": "" },
              { "t": 4.247, "label": "⌘A", "key": "⌘A", "text": "",
                "kind": "combo", "word": "" } ],

  "layers": {
    "order":   ["background", "ripple", "cursor", "keycap"],
    "enabled": { "background": true, "ripple": true, "cursor": true, "keycap": true }
  },

  "meta": { "generated_by": "sclib.plan", "algorithm": "recordly-aligned",
            "click_merge_gap": 2.5, "click_pad": 0.5 }
}
```

### Camera semantics
`camera` is a list of **step targets**, not keyframes. Between regions the
target zoom is 1.0. The renderer springs toward the target (ω=7.5 for zoom,
6.5 for centre), clamps the crop inside `stage`, and never overshoots. This is
why a hand-edited plan still looks smooth: easing is an execution detail, not
something the plan has to encode.

`center` is the **starting** focus for a region, not a fixed lock. While the
region is active the camera keeps a persistent focus and only moves when the
cursor leaves an inner safe zone (25% by default) — ported from Recordly's
`cursorFollowCamera.ts`. The focus freezes while zooming back out. Turning this
off (`camera.follow_cursor = false` in the theme) pins the camera to `center`.

### Pointer styles
`arrow · text · pointer · crosshair · open-hand · closed-hand · resize-ew ·
resize-ns · not-allowed` — the same set Recordly tracks. `sclib.plan` derives
`text` from post-click behaviour (horizontal drag, or cursor staying still in a
text field); a future recorder-level sampler can fill this from the real
`NSCursor` instead, and the renderer needs no change.

### Editing
```bash
rec plan <session>            # (re)derive plan.json
$EDITOR <session>/plan.json   # change a zoom, retime a region, drop a style
rec render <session>          # executes it as-is
rec render <session> --rebuild-plan   # discard edits and re-derive
```

---

## 3. render

`theme.json` supplies style only; the plan supplies decisions. Layer order is
`background → ripple → cursor → keycap`, and each layer can be disabled via
`plan.layers.enabled` or the CLI (`--no-cursor`, `--no-keys`).

### key entries: physical key vs composed text

A keydown carries two different facts, and a key overlay wants either one:

| field | meaning | example |
|---|---|---|
| `key` | what the hand pressed, modifiers included | `⇧A` `␣` `⏎` `⌘C` |
| `label` | the char to show in the default (`text`) view | `a` `␣` `⏎` `⌘C` |
| `text` | what the app actually received | `a` `能` |
| `kind` | `char` \| `ime` \| `combo` \| `special` | |
| `word` | stable name for specials (`return`, `space`, ...) | |

They diverge under an IME: a Chinese typist presses `N E N G` and the app receives
`能`. `theme.keycap.view` picks which one the overlay draws:

```json
"keycap": { "view": "key" }    // N E N G   -- the pinyin letters
"keycap": { "view": "text" }   // 能        -- what landed (default)
```

Text injected with `CGEventKeyboardSetUnicodeString` posts keycode 0, so it has no
physical key at all: `kind` becomes `ime` and `key` falls back to the character.
That is honest — the kit does not invent a keystroke that never happened.
