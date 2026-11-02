"""plan.py — the decision layer between raw facts and pixels.

    facts   meta.json + events.jsonl     recorded, immutable
    plan    plan.json                     decided here, human-editable
    render  theme + plan -> frames        pure execution

Why a separate artifact: "when does the camera zoom, to what, and why" is a
*decision*, not a fact. Putting it in a file makes it inspectable, diffable and
overridable — you can hand-tune one zoom without writing an algorithm, and the
renderer never needs to know whether a human or a heuristic produced the plan.

The auto-focus heuristic follows Recordly's shipped behaviour, taken from
src/components/video-editor/timeline/zoomSuggestionUtils.ts:

    CLICK_CLUSTER_MERGE_GAP_MS = 2500      consecutive clicks merge into one region
    CLICK_CLUSTER_PAD_MS       = 500       padding before first / after last click
    only explicit clicks drive auto-zoom   (their dwell heuristics are computed
                                            and then filtered out for regions)

Schema: see docs/PROTOCOL.md (version 1).
"""

import json
import math
import os

SCHEMA_VERSION = 1

# --- Recordly-aligned tuning -------------------------------------------------
CLICK_MERGE_GAP = 2.5      # seconds — consecutive clicks merge into one region
CLICK_PAD_PRE = 0.25       # start this long before the first click (anticipation)
CLICK_PAD_POST = 1.6       # hold this long after the last interaction
CLICK_MIN_DURATION = 2.0   # never make a region shorter than this
CENTER_BIAS = 0.25         # 0 = frame the action dead-centre, 1 = frame centre
MIN_DWELL = 0.45
MAX_DWELL = 2.6
DWELL_MOVE_FRAC = 0.02     # movement below this fraction of the frame = dwell

MOD_CODES = {55, 56, 57, 58, 59, 60, 61, 62, 63}
MOD_SYMBOLS = [(1 << 18, "\u2303"), (1 << 19, "\u2325"),
               (1 << 17, "\u21e7"), (1 << 20, "\u2318")]
KEY_NAMES = {
    36: "return", 48: "tab", 49: "space", 51: "delete", 53: "esc", 76: "enter",
    115: "home", 116: "page up", 117: "fwd del", 119: "end", 121: "page down",
    123: "left", 124: "right", 125: "down", 126: "up",
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6",
    98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12",
}
# The one-character symbol printed on the key itself. A key overlay has to show
# these, not the control byte the event carries: Tab arrives as "\t", Escape as
# "\x1b", Delete as "\x08" and an arrow as "\x01", none of which is drawable.
SPECIAL_GLYPH = {
    36: "\u23ce", 76: "\u2324", 48: "\u21e5", 49: "\u2423", 51: "\u232b",
    53: "\u238b", 115: "\u2196", 116: "\u21de", 117: "\u2326",
    119: "\u2198", 121: "\u21df", 123: "\u2190", 124: "\u2192",
    125: "\u2193", 126: "\u2191",
}
# ANSI keycode -> the legend on that key (unshifted).
CODE_CHARS = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C",
    9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9",
    26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[",
    34: "I", 35: "P", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";", 42: "\\",
    43: ",", 44: "/", 45: "N", 46: "M", 47: ".",
}
_SHIFTED = {"1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^",
            "7": "&", "8": "*", "9": "(", "0": ")", "-": "_", "=": "+",
            "[": "{", "]": "}", "\\": "|", ";": ":", "'": '"', ",": "<",
            ".": ">", "/": "?", "`": "~"}
SHIFT_BIT = 1 << 17


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------
def read_events(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def to_px(ev, disp):
    ox, oy = disp["origin"]
    s = disp["scale"]
    return (ev.get("x", 0.0) - ox) * s, (ev.get("y", 0.0) - oy) * s


def _printable(s):
    """A key overlay can only draw printable glyphs -- control bytes are not it."""
    return bool(s) and s.isprintable() and not s.isspace()


def key_facts(ev):
    """Split one keydown into (physical key, composed text).

    These are two different facts and a key overlay wants either one:

        key   what the hand did      "⌘C"   "␣"   "⏎"   "N"   "⇧A"
        text  what the app received  --      --    --    "n"   "A"

    With an IME they diverge completely: a Chinese typist presses N-E-N-G and
    the app receives one character "能". Recording both means the viewer can be
    shown either the pinyin keys or the characters, which is the only honest way
    to render Chinese input. Text injected with CGEventKeyboardSetUnicodeString
    carries no physical key at all (the kit posts keycode 0), so `key` falls back
    to the character -- you get the text view, not a fake key view.
    """
    code = int(ev.get("code", -1))
    flags = int(ev.get("flags", 0) or 0)
    text = ev.get("text", "") or ""
    mods = "".join(sym for bit, sym in MOD_SYMBOLS if flags & bit)
    shift = bool(flags & SHIFT_BIT)

    if code in KEY_NAMES:
        glyph = SPECIAL_GLYPH.get(code) or KEY_NAMES[code]
        return {"key": mods + glyph, "label": mods + glyph,
                "text": "", "kind": "special", "word": KEY_NAMES[code]}

    if text == " ":
        # a space that arrived without keycode 49 (injected text) is still a space
        return {"key": mods + "\u2423", "label": mods + "\u2423",
                "text": "", "kind": "special", "word": "space"}

    # Shift is ordinary typing, Command/Control/Option are chords.
    chord = "".join(sym for bit, sym in MOD_SYMBOLS
                    if bit != SHIFT_BIT and flags & bit)
    base = CODE_CHARS.get(code)
    # A keycode cannot produce a non-ASCII character, so text like "能" on a
    # keycode means an IME commit (or injected unicode): there is no physical
    # key to show and pretending keycode 0 is "A" would be a lie.
    ime = _printable(text) and not text.isascii()
    if base is not None and not ime:
        legend = _SHIFTED.get(base, base) if shift else base
        shown = text if _printable(text) else legend
        label = chord + legend if chord else shown
        return {"key": mods + legend, "label": label,
                "text": "" if chord else shown,
                "kind": "combo" if chord else "char", "word": ""}

    if _printable(text):
        return {"key": mods + text, "label": chord + text if chord else text,
                "text": "" if chord else text,
                "kind": "combo" if chord else "ime", "word": ""}
    return {"key": mods + ("#%d" % code), "label": mods + ("#%d" % code),
            "text": "", "kind": "special", "word": ""}


def key_label(ev):
    return key_facts(ev)["label"]


# --------------------------------------------------------------------------
# camera: click clusters -> focus regions
# --------------------------------------------------------------------------
def _clicks(evs, disp):
    out = []
    for e in evs:
        if e.get("type") == "down" and e.get("btn") == "left":
            x, y = to_px(e, disp)
            out.append({"t": e["t"], "x": x, "y": y,
                        "count": int(e.get("count", 1) or 1)})
    out.sort(key=lambda c: c["t"])
    return out


def build_camera(clicks, duration, zoom, base_center, keys=None,
                 merge_gap=CLICK_MERGE_GAP, pre=CLICK_PAD_PRE,
                 post=CLICK_PAD_POST, min_dur=CLICK_MIN_DURATION,
                 center_bias=CENTER_BIAS):
    """One zoom region per click cluster.

    Timing is deliberately asymmetric: a short lead-in, a long hold. A region
    that is 1s long is all ramp-in and ramp-out — the camera never sits still,
    which reads as twitchy. `min_dur` guarantees a settling + hold plateau.

    The focus uses the LAST click of the cluster (where attention actually ends
    up), pulled `center_bias` of the way toward the middle of the stage so the
    frame keeps context instead of parking on whatever was clicked near an edge.
    """
    if not clicks or duration <= 0:
        return []
    clusters = []
    cur = [clicks[0]]
    for c in clicks[1:]:
        if c["t"] - cur[-1]["t"] <= merge_gap:
            cur.append(c)
        else:
            clusters.append(cur)
            cur = [c]
    clusters.append(cur)

    bx, by = base_center
    regions = []
    for cl in clusters:
        first, last = cl[0]["t"], cl[-1]["t"]
        t_ref = cl[-1]

        # typing right after the click is part of the same beat
        if keys:
            near = [k for k in keys if first - 0.6 <= k <= last + 2.5]
            if near:
                last = max(last, near[-1])

        t0 = max(0.0, first - pre)
        t1 = min(duration, last + post)
        if t1 - t0 < min_dur:
            t1 = min(duration, t0 + min_dur)
            if t1 - t0 < min_dur:                 # ran into the end of the take
                t0 = max(0.0, t1 - min_dur)
        if t1 <= t0:
            continue

        cx = t_ref["x"] + (bx - t_ref["x"]) * center_bias
        cy = t_ref["y"] + (by - t_ref["y"]) * center_bias

        if regions and t0 <= regions[-1]["t1"] + 0.2:
            m = regions[-1]
            m["t1"] = max(m["t1"], t1)
            m["center"] = [round((m["center"][0] + cx) / 2.0, 1),
                           round((m["center"][1] + cy) / 2.0, 1)]
            m["clicks"] += len(cl)
            continue
        regions.append({"t0": round(t0, 3), "t1": round(t1, 3),
                        "zoom": round(zoom, 3),
                        "center": [round(cx, 1), round(cy, 1)],
                        "reason": "click-cluster", "clicks": len(cl)})
    return regions


# --------------------------------------------------------------------------
# cursor style: pointer state over time
# --------------------------------------------------------------------------
def build_cursor_styles(evs, disp, clicks, size, duration):
    """Pointer-state segments derived from post-click behaviour.

    Mirrors Recordly's classifyPostClickBehavior(): horizontal drags and
    still-cursor-after-click both mean "you are in a text field", which is what
    makes an I-beam the right pointer there.
    """
    W, H = size
    moves = [e for e in evs if e.get("type") == "move"]
    ups = [e for e in evs if e.get("type") == "up" and e.get("btn") == "left"]
    segs = []

    # Direct measurements win: if the recorder sampled the element under the
    # pointer, use it. The click heuristics below are the fallback for takes
    # recorded before this existed (or where Accessibility was unavailable).
    measured = [e for e in evs if e.get("type") == "cursor"]
    if measured:
        for i, e in enumerate(measured):
            t0 = max(0.0, float(e["t"]))
            t1 = float(measured[i + 1]["t"]) if i + 1 < len(measured) else duration
            t1 = min(duration, t1)
            if t1 > t0:
                segs.append({"t0": round(t0, 3), "t1": round(t1, 3),
                             "style": e.get("shape") or "arrow",
                             "reason": "ax:" + (e.get("role") or "?")})
        return segs

    def add(t0, t1, style, reason):
        t0 = max(0.0, t0)
        t1 = min(duration, t1)
        if t1 <= t0:
            return
        segs.append({"t0": round(t0, 3), "t1": round(t1, 3),
                     "style": style, "reason": reason})

    for c in clicks:
        kind = _classify(evs, c, moves, ups, W, H)
        if kind in ("text-selection", "text-field-click", "double-click-like"):
            add(c["t"] - 0.1, c["t"] + 1.4, "text", kind)
        elif kind == "dropdown-open":
            add(c["t"] - 0.1, c["t"] + 1.2, "arrow", kind)

    segs.sort(key=lambda s: s["t0"])
    merged = []
    for s in segs:
        if merged and s["t0"] <= merged[-1]["t1"] + 0.2:
            merged[-1]["t1"] = max(merged[-1]["t1"], s["t1"])
            if s["style"] != merged[-1]["style"]:
                merged[-1]["style"] = s["style"]
        else:
            merged.append(dict(s))
    return merged


def _classify(evs, click, moves, ups, W, H):
    if click.get("count", 1) >= 2:
        return "double-click-like"

    up = next((u for u in ups if 0 < u["t"] - click["t"] < 3.0), None)
    if up:
        dx = abs(up["x"] - click["x"])
        dy = abs(up["y"] - click["y"])
        dur = up["t"] - click["t"]
        if dur >= 0.2 and dx / max(W, 1) > 0.03 and dx > dy * 1.8:
            return "text-selection"

    window = [m for m in moves if click["t"] + 0.1 < m["t"] <= click["t"] + 2.0]
    if len(window) < 3:
        return "text-field-click"

    total_dx = sum(abs(m["x"] - click["x"]) for m in window)
    total_dy = sum(abs(m["y"] - click["y"]) for m in window)
    max_dist = max(math.hypot(m["x"] - click["x"], m["y"] - click["y"])
                   for m in window)
    if max_dist / max(W, 1) < 0.02:
        return "text-field-click"

    net_dy = window[-1]["y"] - click["y"]
    if net_dy / max(H, 1) > 0.03 and total_dy > total_dx * 1.5:
        return "dropdown-open"
    if total_dx / max(W, 1) > 0.03 and total_dx > total_dy * 1.8:
        return "text-selection"
    return "click-like"


def style_at(segments, t, default="arrow"):
    for s in segments:
        if s["t0"] <= t <= s["t1"]:
            return s["style"]
    return default


# --------------------------------------------------------------------------
# stage (what the camera may move inside)
# --------------------------------------------------------------------------
def build_stage(evs, meta, duration):
    target = meta.get("target", "screen")
    W, H = int(meta["width"]), int(meta["height"])
    if target != "window":
        return {"target": "screen", "rects": [{"t": 0.0, "rect": [0, 0, W, H]}]}
    sync = float(meta.get("sync_offset") or 0.0)
    rects = []
    for e in evs:
        if e.get("type") != "win":
            continue
        r = ((e["px"], e["py"], e["pw"], e["ph"]) if e.get("pin")
             else (e["fx"], e["fy"], e["fw"], e["fh"]))
        if r[2] <= 2 or r[3] <= 2:
            continue
        rects.append({"t": round(e["t"] - sync, 3),
                      "rect": [round(v, 1) for v in r]})
    if not rects:
        return {"target": "screen", "rects": [{"t": 0.0, "rect": [0, 0, W, H]}]}
    return {"target": "window", "window": (meta.get("window") or {}).get("id"), "rects": rects}


# --------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------
def build_plan(sess, theme=None, zoom=None):
    meta = json.load(open(os.path.join(sess, "meta.json")))
    evs = read_events(os.path.join(sess, "events.jsonl"))
    disp = meta["display"]
    sync = float(meta.get("sync_offset") or 0.0)
    duration = float(meta["duration"])
    W, H = int(meta["width"]), int(meta["height"])

    if zoom is None:
        zoom = float((theme or {}).get("camera", {}).get("zoom", 1.45))

    # shift event time onto the video clock once, here
    for e in evs:
        if "t" in e:
            e["_vt"] = e["t"] - sync

    clicks = [{"t": round(c["t"] - sync, 3), "x": round(c["x"], 1),
               "y": round(c["y"], 1), "button": "left", "count": c["count"]}
              for c in _clicks(evs, disp)]
    clicks = [c for c in clicks if -0.2 <= c["t"] <= duration]

    stage = build_stage(evs, meta, duration)
    rect0 = (stage.get("rects") or [{"rect": [0, 0, W, H]}])[0]["rect"]
    base_center = (rect0[0] + rect0[2] / 2.0, rect0[1] + rect0[3] / 2.0)

    key_times = [e["_vt"] for e in evs
                 if e.get("type") == "keydown" and not e.get("repeat")
                 and e.get("code", -1) not in MOD_CODES
                 and -0.1 <= e["_vt"] <= duration]
    camera_cfg = (theme or {}).get("camera", {})
    camera = build_camera(
        clicks, duration, zoom, base_center, keys=key_times,
        pre=float(camera_cfg.get("pad_pre", CLICK_PAD_PRE)),
        post=float(camera_cfg.get("pad_post", CLICK_PAD_POST)),
        min_dur=float(camera_cfg.get("min_duration", CLICK_MIN_DURATION)),
        center_bias=float(camera_cfg.get("center_bias", CENTER_BIAS)))

    cursor_segs = build_cursor_styles(
        [dict(e, t=e["_vt"]) for e in evs], disp, clicks, (W, H), duration)

    keys = []
    for e in evs:
        if e.get("type") == "keydown" and not e.get("repeat") \
                and e.get("code", -1) not in MOD_CODES:
            tv = e["_vt"]
            if -0.1 <= tv <= duration:
                f = key_facts(e)
                keys.append({"t": round(tv, 3), "label": f["label"],
                             "key": f["key"], "text": f["text"],
                             "kind": f["kind"], "word": f["word"],
                             "raw": f["text"] or f["label"]})

    plan = {
        "version": SCHEMA_VERSION,
        "source": {
            "video": os.path.basename(meta["video"]),
            "width": W, "height": H,
            "fps": int(meta.get("fps") or 30),
            "duration": round(duration, 3),
            "display": disp,
            "sync_offset": round(sync, 4),
        },
        "stage": stage,
        "camera": camera,
        "cursor": {
            "default_style": "arrow",
            "smoothing": float((theme or {}).get("camera", {}).get("smoothing", 22.0)),
            "styles": cursor_segs,
        },
        "clicks": clicks,
        "keys": keys,
        "layers": {
            "order": ["background", "ripple", "cursor", "keycap"],
            "enabled": {"background": True, "ripple": True,
                        "cursor": True, "keycap": True},
        },
        "meta": {"generated_by": "sclib.plan", "algorithm": "recordly-aligned",
                 "click_merge_gap": CLICK_MERGE_GAP,
                 "pad_pre": CLICK_PAD_PRE, "pad_post": CLICK_PAD_POST,
                 "min_duration": CLICK_MIN_DURATION,
                 "center_bias": CENTER_BIAS},
    }
    return plan


def save(plan, path):
    with open(path, "w") as f:
        json.dump(plan, f, indent=2)
    return path


def load(path):
    with open(path) as f:
        return json.load(f)
