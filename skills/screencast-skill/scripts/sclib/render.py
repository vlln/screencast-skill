"""Renderer: facts + plan + theme -> frames. numpy + Pillow only.

This module is *execution*. It makes no decisions about when to zoom or which
pointer to show — that came from plan.json. It only knows how to turn a plan
into pixels, using the independent layers in sclib.layers.

No OpenCV: the camera move is an axis-aligned crop+resample, i.e.
`Image.resize(box=...)`, measured at 15.7ms for a 2560x1440 frame.
"""

import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image

from . import plan as plan_mod
from .layers import Background, cursor_layer, keycap_layer, ripple_layer
from .layers.base import paste_rgba

FFMPEG = os.environ.get("REC_FFMPEG") or "ffmpeg"
FFPROBE = os.environ.get("REC_FFPROBE") or "ffprobe"


def has_audio(path):
    """Does the captured file carry an audio track we should preserve?"""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True)
        return "audio" in (r.stdout or "")
    except Exception:
        return False


# --------------------------------------------------------------------------
# camera evaluation (springed execution of the plan's step targets)
# --------------------------------------------------------------------------
def cd_step(x, v, target, omega, dt):
    d = x - target
    e = math.exp(-omega * dt)
    return (target + (d + (v + omega * d) * dt) * e,
            (v - omega * (v + omega * d) * dt) * e)


def camera_path(regions, nframes, fps, base_rect, cursor=None, follow=True,
                safe_zone=0.35, follow_omega=5.0, zoom_omega=7.5,
                min_move_interval=0.30, dead_zone=0.02):
    """Evaluate the plan's camera, with Recordly-style cursor following.

    The focus is a *persistent* value, not a per-frame recompute: inside the
    safe zone the camera does not move at all. Three deliberate departures from
    the naive port, all to stop the frame from sliding around:

      * when the cursor leaves the safe zone the focus moves the MINIMUM amount
        that puts the cursor back on the zone edge, instead of jumping all the
        way to the cursor (Recordly jumps to the cursor, which makes every
        small drift re-target and the whole frame creeps);
      * re-targets are throttled by `min_move_interval`;
      * the initial focus is the cursor position at region start, not the click
        centroid — otherwise the camera makes two moves (to centroid, then to
        cursor) right when the zoom begins.

    While zooming back out the focus is frozen so the framing holds until the
    zoom is gone.
    """
    tz = np.ones(nframes)
    region_center = [None] * nframes
    region_follow = [None] * nframes
    for r in regions:
        a = max(0, int(float(r["t0"]) * fps))
        b = min(nframes, int(float(r["t1"]) * fps))
        if b <= a:
            continue
        tz[a:b] = float(r["zoom"])
        # a region may pin the camera ("follow": false) so an authored `center`
        # is honoured literally instead of being taken over by the cursor
        rf = bool(r.get("follow", True))
        for i in range(a, b):
            region_center[i] = (float(r["center"][0]), float(r["center"][1]))
            region_follow[i] = rf

    zs = np.ones(nframes)
    xs = np.zeros(nframes)
    ys = np.zeros(nframes)

    z, vz = 1.0, 0.0
    fx = fy = None
    cx = cy = None
    vcx = vcy = 0.0
    last_move = -1e9
    dt = 1.0 / fps

    for i in range(nframes):
        z, vz = cd_step(z, vz, float(tz[i]), zoom_omega, dt)
        z = min(max(z, 1.0), 4.0)
        bx, by, bw, bh = base_rect[i]
        zoomed = float(tz[i]) > 1.001

        if zoomed:
            follow_here = follow and (region_follow[i] if region_follow[i] is not None else True)
            if fx is None:
                # start where the cursor already is, unless the region is pinned
                if follow_here and cursor is not None:
                    fx, fy = float(cursor[i][0]), float(cursor[i][1])
                else:
                    rc = region_center[i]
                    fx, fy = (rc if rc else (bx + bw / 2.0, by + bh / 2.0))
                cx, cy = fx, fy
                vcx = vcy = 0.0
                last_move = i / fps

            if follow_here and cursor is not None and (i / fps - last_move) >= min_move_interval:
                halfw, halfh = (bw / z) / 2.0, (bh / z) / 2.0
                insx, insy = halfw * 2.0 * safe_zone, halfh * 2.0 * safe_zone
                # margins for the safe rect, plus a small hysteresis
                mx_ = dead_zone * halfw * 2.0
                my_ = dead_zone * halfh * 2.0
                room_x = max(1.0, halfw - insx - mx_)
                room_y = max(1.0, halfh - insy - my_)
                px, py = float(cursor[i][0]), float(cursor[i][1])
                nfx, nfy = fx, fy
                if px < fx - room_x:
                    nfx = px + room_x
                elif px > fx + room_x:
                    nfx = px - room_x
                if py < fy - room_y:
                    nfy = py + room_y
                elif py > fy + room_y:
                    nfy = py - room_y
                if nfx != fx or nfy != fy:
                    fx, fy = nfx, nfy
                    last_move = i / fps
            tcx, tcy = fx, fy
        else:
            if z <= 1.02:
                fx = fy = None
            tcx, tcy = (fx, fy) if fx is not None else (bx + bw / 2.0, by + bh / 2.0)

        if cx is None:
            cx, cy = tcx, tcy
        cx, vcx = cd_step(cx, vcx, float(tcx), follow_omega, dt)
        cy, vcy = cd_step(cy, vcy, float(tcy), follow_omega, dt)

        cw, ch = bw / z, bh / z
        xs[i] = min(max(cx - cw / 2.0, bx), bx + bw - cw)
        ys[i] = min(max(cy - ch / 2.0, by), by + bh - ch)
        zs[i] = z
    return zs, xs, ys


def base_rect_track(stage, nframes, fps):
    rects = stage.get("rects") or []
    if not rects:
        return [(0.0, 0.0, 1.0, 1.0)] * nframes
    rects = sorted(rects, key=lambda r: float(r["t"]))
    out = []
    idx = 0
    cur = rects[0]["rect"]
    for i in range(nframes):
        t = i / fps
        while idx + 1 < len(rects) and float(rects[idx + 1]["t"]) <= t:
            idx += 1
            cur = rects[idx]["rect"]
        out.append(tuple(float(v) for v in cur))
    sm = []
    c = list(out[0])
    v = [0.0, 0.0, 0.0, 0.0]
    dt = 1.0 / fps
    for r in out:
        for k in range(4):
            c[k], v[k] = cd_step(c[k], v[k], r[k], 16.0, dt)
        sm.append(tuple(c))
    return sm


def cursor_track(evs, disp, nframes, fps, sync, omega=22.0):
    samples = []
    for e in evs:
        if e.get("type") in ("move", "down", "up", "scroll"):
            tv = e["t"] - sync
            if tv < -0.5:
                continue
            x, y = plan_mod.to_px(e, disp)
            samples.append((tv, x, y))
    if not samples:
        return np.zeros((nframes, 2), dtype=np.float32)
    samples.sort(key=lambda s: s[0])
    ts = np.array([s[0] for s in samples])
    xs = np.array([s[1] for s in samples])
    ys = np.array([s[2] for s in samples])
    tgrid = np.arange(nframes) / fps
    rx = np.interp(tgrid, ts, xs)
    ry = np.interp(tgrid, ts, ys)
    out = np.zeros((nframes, 2), dtype=np.float32)
    px, py = float(rx[0]), float(ry[0])
    vx = vy = 0.0
    dt = 1.0 / fps
    for i in range(nframes):
        if abs(rx[i] - px) > 0.25 or abs(ry[i] - py) > 0.25:
            px, vx = cd_step(px, vx, float(rx[i]), omega, dt)
            py, vy = cd_step(py, vy, float(ry[i]), omega, dt)
        out[i] = (px, py)
    return out


def cursor_scales(ctrack, clicks, fps, cfg=None):
    """Auto-scale the pointer the way Recordly does.

    Recordly's pointer does not read as a fixed sprite: it grows when the hand
    slows down to aim, gives a short pulse on click, and shrinks while it
    travels. This reproduces that from the recorded track alone -- speed is the
    only signal needed, and it is already smoothed by the critically damped
    follower, so the result breathes instead of twitching.
    """
    cfg = cfg or {}
    n = len(ctrack)
    if n == 0:
        return np.ones(0, dtype=np.float64)
    gain = float(cfg.get("gain", 0.34))        # extra size at a full stop
    vref = float(cfg.get("speed_ref", 900.0))  # px/s that counts as "moving"
    pulse = float(cfg.get("click_pulse", 0.11))
    omega = float(cfg.get("smoothing", 10.0))
    dt = 1.0 / max(1e-6, fps)

    raw = np.ones(n, dtype=np.float64)
    for i in range(1, n):
        v = math.hypot(float(ctrack[i][0] - ctrack[i - 1][0]),
                       float(ctrack[i][1] - ctrack[i - 1][1])) / dt
        slow = max(0.0, 1.0 - v / vref)
        raw[i] = 1.0 + gain * (slow ** 1.15)

    out = np.ones(n, dtype=np.float64)
    x, v = raw[0], 0.0
    for i in range(n):
        x, v = cd_step(x, v, float(raw[i]), omega, dt)
        out[i] = x

    for cl in clicks:                    # a click is a beat: pop, then settle
        i0 = int(round(cl["t"] * fps))
        for d in range(int(0.25 * fps) + 1):
            j = i0 + d
            if 0 <= j < n:
                out[j] += pulse * math.exp(-d / (0.06 * fps))
    return out


# --------------------------------------------------------------------------
def _deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def build_timeline(theme, base_pad):
    """Flatten theme.timeline into [(t, effective_theme)].

    A timeline entry only *overrides* what it names, so a beat can change the
    cursor without touching the background, and later change the background
    without disturbing the cursor. That is what makes a single take able to
    demonstrate one capability at a time.
    """
    base = {k: v for k, v in (theme or {}).items() if k != "timeline"}
    bg = dict(base.get("background") or {})
    bg["pad"] = base_pad          # pad must stay constant or the encoder size drifts
    base["background"] = bg

    entries = sorted((theme or {}).get("timeline") or [],
                     key=lambda e: float(e.get("t", 0)))
    out = []
    cur = base
    if entries and float(entries[0].get("t", 0)) > 0:
        out.append((0.0, base))   # before the first entry, the base theme rules
    for e in entries:
        cur = _deep_merge(cur, e.get("set") or {})
        b = dict(cur.get("background") or {})
        b["pad"] = base_pad
        cur = dict(cur, background=b)
        out.append((float(e.get("t", 0)), cur))
    return out or [(0.0, base)]


class BackgroundSwitcher:
    """Caches one Background per config and cross-fades between them."""

    def __init__(self, size, fade=0.35):
        self.size = size
        self.fade = fade
        self.cache = {}
        self.key = None
        self.prev = None
        self.t0 = None

    def _get(self, cfg):
        key = json.dumps(cfg, sort_keys=True)
        if key not in self.cache:
            self.cache[key] = Background(cfg, self.size)
        return key, self.cache[key]

    def compose(self, cfg, content, t):
        key, bg = self._get(cfg)
        if self.key is None:
            self.key = key
            return bg.compose(content)
        if key != self.key:
            self.prev = self.cache.get(self.key)
            self.key = key
            self.t0 = t
        if self.prev is not None and self.t0 is not None and t - self.t0 < self.fade:
            a = max(0.0, min(1.0, (t - self.t0) / self.fade))
            return Image.blend(self.prev.compose(content), bg.compose(content), a)
        self.prev = None
        return bg.compose(content)


def render_session(args):
    sess = os.path.abspath(args.session)
    meta_path = os.path.join(sess, "meta.json")
    if not os.path.exists(meta_path):
        raise SystemExit("no meta.json in %s — did you run `rec stop`?" % sess)
    meta = json.load(open(meta_path))
    theme = getattr(args, "theme_data", None) or {}

    plan_path = getattr(args, "plan", None) or os.path.join(sess, "plan.json")
    if os.path.exists(plan_path) and not getattr(args, "rebuild_plan", False):
        plan = plan_mod.load(plan_path)
    else:
        plan = plan_mod.build_plan(sess, theme, None)
        plan_mod.save(plan, plan_path)

    disp = meta["display"]
    sync = float(meta.get("sync_offset") or 0.0)
    fps = int(getattr(args, "fps", 0) or meta.get("fps") or 30)
    video = meta["video"]
    if not os.path.isabs(video):
        video = os.path.join(sess, video)
    evs = plan_mod.read_events(os.path.join(sess, "events.jsonl"))

    W, H = int(meta["width"]), int(meta["height"])
    duration = float(meta["duration"])
    nframes = max(1, int(round(duration * fps)))

    base = base_rect_track(plan["stage"], nframes, fps)
    bx0, by0, bw0, bh0 = base[0]
    ow = int(round(min(bw0, W))) // 2 * 2
    oh = int(round(min(bh0, H))) // 2 * 2
    ow, oh = max(ow, 16), max(oh, 16)

    smoothing = float((plan.get("cursor") or {}).get("smoothing", 22.0))
    if getattr(args, "smooth", None):
        smoothing = float(args.smooth)
    ctrack = cursor_track(evs, disp, nframes, fps, sync, smoothing)

    camera_cfg = theme.get("camera") or {}
    camera = [] if getattr(args, "no_zoom", False) else list(plan.get("camera", []))
    if getattr(args, "zoom_override", None) is not None:
        camera = [dict(r, zoom=float(args.zoom_override)) for r in camera]
    zs, xs, ys = camera_path(
        camera, nframes, fps, base,
        cursor=ctrack,
        follow=bool(camera_cfg.get("follow_cursor", True)),
        safe_zone=float(camera_cfg.get("safe_zone", 0.35)),
        follow_omega=float(camera_cfg.get("follow_omega", 5.0)),
        min_move_interval=float(camera_cfg.get("min_move_interval", 0.30)),
        zoom_omega=float(camera_cfg.get("zoom_omega", 7.5)))

    styles = (plan.get("cursor") or {}).get("styles", [])
    default_style = (plan.get("cursor") or {}).get("default_style", "arrow")
    clicks = plan.get("clicks", [])
    cscale = cursor_scales(ctrack, clicks, fps,
                           (plan.get("cursor") or {}).get("auto_scale"))
    # pass the plan entries through untouched: the keycap layer needs both the
    # physical key and the composed text to honour its "view" mode
    keys = plan.get("keys", [])

    enabled = dict((plan.get("layers") or {}).get("enabled", {}))
    if getattr(args, "no_cursor", False):
        enabled["cursor"] = False
    if getattr(args, "no_keys", False):
        enabled["keycap"] = False

    cursor_size = float(getattr(args, "cursor_size", None)
                        or (base_cursor.get("size") or 40.0))
    cur_h = max(10, cursor_size * oh / 1080.0)
    scale_ref = oh / 1080.0

    bg_cfg = dict(theme.get("background") or {})
    if getattr(args, "pad", None) is not None:
        bg_cfg["pad"] = int(args.pad)
    base_pad = int(bg_cfg.get("pad", 0))
    timeline = build_timeline(theme, base_pad)
    if getattr(args, "radius", None) is not None:
        bg_cfg["radius"] = int(args.radius)
    if getattr(args, "bg", None):
        cols = [c.strip() for c in args.bg.split(",")]
        bg_cfg["type"] = "gradient" if len(cols) > 1 else "solid"
        bg_cfg["top"] = cols[0]
        bg_cfg["bottom"] = cols[1] if len(cols) > 1 else cols[0]
        bg_cfg["color"] = cols[0]
    bg_switcher = BackgroundSwitcher((ow, oh))

    base_keycap = theme.get("keycap")
    base_ripple = theme.get("ripple")
    base_cursor = theme.get("cursor") or {}
    cursor_assets = base_cursor.get("assets")
    if isinstance(cursor_assets, str):
        ap = cursor_assets
        if not os.path.isabs(ap):
            ap = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), ap)
        try:
            cursor_assets = json.load(open(ap)).get("cursors")
        except Exception:
            cursor_assets = None
    ripple_dur = float(ripple_layer.cfg(base_ripple)["duration"])

    out_w = (ow + 2 * base_pad) // 2 * 2
    out_hh = (oh + 2 * base_pad) // 2 * 2
    out_path = args.out or os.path.join(sess, "out.mp4")

    # Audio is a pass-through, not a re-render: the raw take already holds the
    # microphone / system track, so the effects video just carries it over.
    audio_in = has_audio(video) if getattr(args, "keep_audio", True) else False
    enc_cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (out_w, out_hh),
               "-r", str(fps), "-i", "-"]
    if audio_in:
        enc_cmd += ["-i", video]
    enc_cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
                "-pix_fmt", "yuv420p"]
    if audio_in:
        enc_cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
                    "-shortest"]
    enc_cmd += ["-movflags", "+faststart", out_path]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    dec = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", video,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-vsync", "0", "-"],
        stdout=subprocess.PIPE)

    tl_idx = 0
    fsz = W * H * 3
    for i in range(nframes):
        raw = dec.stdout.read(fsz)
        if not raw or len(raw) < fsz:
            break
        frame = Image.fromarray(np.frombuffer(raw, np.uint8).reshape(H, W, 3))

        z = float(zs[i])
        bx, by, bw, bh = base[i]
        cw = max(2.0, min(bw / z, bw))
        ch = max(2.0, min(bh / z, bh))
        cx = min(max(float(xs[i]) + cw / 2.0, bx + cw / 2.0), bx + bw - cw / 2.0)
        cy = min(max(float(ys[i]) + ch / 2.0, by + ch / 2.0), by + bh - ch / 2.0)
        x0, y0 = cx - cw / 2.0, cy - ch / 2.0

        if (abs(z - 1.0) < 1e-4 and abs(x0 - bx0) < 0.5 and abs(y0 - by0) < 0.5
                and abs(cw - ow) < 0.5 and abs(ch - oh) < 0.5):
            content = frame.crop((int(bx0), int(by0), int(bx0) + ow, int(by0) + oh))
            if content.size != (ow, oh):
                content = frame.resize((ow, oh), Image.LANCZOS)
        else:
            content = frame.resize((ow, oh), Image.BICUBIC,
                                   box=(x0, y0, x0 + cw, y0 + ch))
        if content.mode != "RGB":
            content = content.convert("RGB")

        mx, my = ow / cw, oh / ch
        t = i / fps

        while tl_idx + 1 < len(timeline) and timeline[tl_idx + 1][0] <= t:
            tl_idx += 1
        active = timeline[tl_idx][1]
        keycap_style = active.get("keycap", base_keycap)
        ripple_style = active.get("ripple", base_ripple)
        cursor_theme = active.get("cursor") or base_cursor
        dark_cursor = bool(cursor_theme.get("dark", True))

        # ---- layers: independent, and each one can be switched off ----
        if enabled.get("ripple", True):
            for cl in clicks:
                age = t - cl["t"]
                if 0 <= age <= ripple_dur:
                    ripple_layer.draw(content, (cl["x"] - x0) * mx,
                                      (cl["y"] - y0) * my, age, scale_ref,
                                      ripple_style)

        if enabled.get("cursor", True):
            cp = ctrack[i]
            ox_, oy_ = (cp[0] - x0) * mx, (cp[1] - y0) * my
            if -40 <= ox_ <= ow + 40 and -40 <= oy_ <= oh + 40:
                # a theme may pin the pointer art (used to show off styles)
                style = cursor_theme.get("force_style") or plan_mod.style_at(
                    styles, t, default_style)
                press = ripple_layer.press_factor(clicks, t, ripple_style)
                # Cursor sizing. Recordly itself sizes the pointer by viewport
                # width (cursorScale.ts: viewport/1920) and therefore keeps it
                # CONSTANT under zoom -- "none" below reproduces that exactly.
                # "partial" grows it slowly so it does not look tiny against a
                # magnified UI; "match" scales it 1:1 with the camera.
                mode = cursor_theme.get("scale_with_zoom", "partial")
                if mode == "none":
                    grow = 1.0
                elif mode == "match":
                    grow = z
                else:
                    grow = 1.0 + (z - 1.0) * float(cursor_theme.get("zoom_factor", 0.35))
                # auto-scale: bigger while aiming slowly, smaller when travelling
                sprite, hx, hy = cursor_layer.build(
                    style, cur_h * grow * press * cscale[i],
                    dark_cursor, cursor_assets)
                paste_rgba(content, sprite, ox_ - hx, oy_ - hy)

        if enabled.get("keycap", True):
            keycap_layer.draw(content, keys, t, keycap_style, oh, ow)

        content = bg_switcher.compose(active.get("background") or bg_cfg, content, t)

        enc.stdin.write(np.asarray(content, np.uint8).tobytes())
        if i % 30 == 0:
            sys.stderr.write("\r  rendering %d/%d" % (i + 1, nframes))
            sys.stderr.flush()

    sys.stderr.write("\r  rendering %d/%d\n" % (nframes, nframes))
    try:
        enc.stdin.close()
    except BrokenPipeError:
        pass
    enc.wait()
    dec.kill()
    if enc.returncode != 0:
        raise SystemExit("encode failed (rc=%s)" % enc.returncode)
    print(out_path)
    return 0
