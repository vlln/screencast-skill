"""Background layer: the styled "card" the recording sits on.

Independent of every other layer, and configured entirely from theme. The knobs
mirror what a video tool actually exposes (Recordly ships gradient / solid /
wallpaper, blur, padding, radius, drop shadow; this adds multi-stop gradients,
an angle, vignette and grain, which is what separates a flat two-colour wash
from a background that looks designed):

    "background": {
      "type": "gradient",              # gradient | solid | image
      "stops": [                       # optional; overrides top/bottom
        {"at": 0.0, "color": "#0b1220"},
        {"at": 0.55, "color": "#1b3b63"},
        {"at": 1.0, "color": "#2a1b4d"}
      ],
      "top": "#0b1220", "bottom": "#1e3a5f",
      "angle": 135,                    # degrees; 90 = top->bottom
      "color": "#111827",              # type=solid
      "image": "wallpapers/x.png",     # type=image, relative to the skill dir
      "blur": 0, "dim": 0.0,           # image only: backdrop blur, darkening
      "pad": 90, "radius": 20,
      "shadow": {"blur": 34, "dy": 20, "alpha": 150},
      "vignette": 0.22,                # darkens the corners; 0 = off
      "grain": 0.035                   # film grain; 0 = off
    }

`shadow: true|false` still works and maps onto the defaults. Everything is
built once per render, so the per-frame cost is one paste.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SHADOW_DEFAULTS = {"blur": 34.0, "dy": 20.0, "alpha": 150, "scale": 1.0}


def _hex(h, fallback=(17, 24, 39)):
    if not h:
        return fallback
    if isinstance(h, (list, tuple)):
        return tuple(int(v) for v in h[:3])
    h = str(h).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


class Background:
    def __init__(self, cfg, content_size):
        self.cfg = cfg or {}
        self.ow, self.oh = content_size
        self.pad = int(self.cfg.get("pad", 0))
        self.radius = int(self.cfg.get("radius", 16))
        self.W = self.ow + 2 * self.pad
        self.H = self.oh + 2 * self.pad
        self.enabled = self.pad > 0
        if not self.enabled:
            return

        self._image = self._build_image()
        self._add_shadow()
        self._add_frame_light()
        self.mask = self._build_mask()

    # -- construction ------------------------------------------------------
    def _stops(self):
        raw = self.cfg.get("stops")
        if raw:
            out = [(_float(p.get("at"), 0.0), _hex(p.get("color")))
                   for p in raw if isinstance(p, dict)]
            out.sort(key=lambda s: s[0])
            if len(out) >= 2:
                return out
        return [(0.0, _hex(self.cfg.get("top"), (11, 18, 32))),
                (1.0, _hex(self.cfg.get("bottom"), (30, 58, 95)))]

    def _gradient(self):
        """Linear gradient at an arbitrary angle, with N stops."""
        stops = self._stops()
        angle = np.radians(float(self.cfg.get("angle", 90.0)))
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        # project onto the gradient direction and normalise into 0..1
        dx, dy = np.cos(angle), np.sin(angle)
        t = (xx * dx + yy * dy)
        t -= t.min()
        span = max(1e-6, t.max())
        t /= span

        out = np.zeros((self.H, self.W, 3), np.float32)
        for i in range(len(stops) - 1):
            a, ca = stops[i]
            b, cb = stops[i + 1]
            hi = (t <= b) if i == len(stops) - 2 else (t < b)
            m = (t >= a) & hi
            if not m.any():
                continue
            local = ((t[m] - a) / max(1e-6, b - a))[..., None]
            ca_ = np.array(ca, np.float32)
            cb_ = np.array(cb, np.float32)
            out[m] = ca_ + (cb_ - ca_) * local
        # anything the masks missed (float edges) takes the last stop
        empty = (out.sum(axis=2) == 0)
        if empty.any():
            out[empty] = np.array(stops[-1][1], np.float32)
        return Image.fromarray(out.astype(np.uint8))

    def _build_image(self):
        kind = self.cfg.get("type", "gradient")

        if kind == "image" and self.cfg.get("image"):
            path = self.cfg["image"]
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))), path)
            try:
                im = Image.open(path).convert("RGB")
                # cover, not stretch: cropping keeps photographs undistorted
                s = max(self.W / im.width, self.H / im.height)
                im = im.resize((max(1, int(im.width * s)),
                                max(1, int(im.height * s))), Image.LANCZOS)
                left = (im.width - self.W) // 2
                top = (im.height - self.H) // 2
                im = im.crop((left, top, left + self.W, top + self.H))
                blur = float(self.cfg.get("blur", 0) or 0)
                if blur > 0:
                    im = im.filter(ImageFilter.GaussianBlur(blur))
                dim = float(self.cfg.get("dim", 0) or 0)
                if dim > 0:
                    im = Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), dim)
                return im
            except Exception:
                pass  # fall through to gradient

        if kind == "solid":
            return Image.new("RGB", (self.W, self.H),
                             _hex(self.cfg.get("color"), (17, 24, 39)))

        img = self._gradient()
        img = self._apply_vignette(img)
        img = self._apply_grain(img)
        return img

    def _apply_vignette(self, img):
        v = float(self.cfg.get("vignette", 0.0) or 0.0)
        if v <= 0:
            return img
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        cx, cy = self.W / 2.0, self.H / 2.0
        r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / 1.4142
        fade = np.clip(1.0 - v * (r ** 1.6) * 1.6, 0.0, 1.0)[..., None]
        arr = np.asarray(img, np.float32) * fade
        return Image.fromarray(arr.clip(0, 255).astype(np.uint8))

    def _apply_grain(self, img):
        g = float(self.cfg.get("grain", 0.0) or 0.0)
        if g <= 0:
            return img
        rng = np.random.default_rng(7)          # deterministic across frames
        noise = rng.normal(0.0, 255.0 * g, (self.H, self.W, 1)).astype(np.float32)
        arr = np.asarray(img, np.float32) + noise
        return Image.fromarray(arr.clip(0, 255).astype(np.uint8))

    def _add_shadow(self):
        cfg = self.cfg.get("shadow", True)
        if cfg is False:
            return
        opts = dict(SHADOW_DEFAULTS)
        if isinstance(cfg, dict):
            opts.update(cfg)
        elif self.cfg.get("pad", 0) <= 0:
            return

        blur = max(1.0, float(opts.get("blur", 34.0)))
        dy = float(opts.get("dy", 20.0))
        alpha = int(opts.get("alpha", 150))
        inset = blur * 1.2
        layer = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(layer)
        # shrink the light-leak: shadow spreads from under the card, so it is
        # drawn from a slightly inset shape and never touches the frame edge
        d.rounded_rectangle(
            [self.pad + inset * 0.25, self.pad + dy * 0.35,
             self.pad + self.ow - inset * 0.25, self.pad + self.oh + dy],
            radius=max(1, self.radius), fill=alpha)
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        self._image.paste(Image.new("RGB", (self.W, self.H), (6, 8, 12)),
                          (0, 0), layer)

    def _add_frame_light(self):
        """A hairline on the card's top edge, the way a lit surface catches it."""
        a = int(self.cfg.get("edge_light", 46) or 0)
        if a <= 0:
            return
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [self.pad, self.pad, self.pad + self.ow - 1, self.pad + self.oh - 1],
            radius=max(1, self.radius),
            outline=(255, 255, 255, a), width=1)
        mask = Image.new("L", (self.W, self.H), 0)
        ImageDraw.Draw(mask).rectangle(
            [0, 0, self.W, self.pad + self.oh // 3], fill=255)
        self._image.paste(layer, (0, 0), Image.composite(
            layer.split()[3], Image.new("L", (self.W, self.H), 0), mask))

    def _build_mask(self):
        r = max(0, min(self.radius, min(self.ow, self.oh) // 2))
        S = 4 if self.ow * self.oh <= 6_000_000 else 2
        m = Image.new("L", (self.ow * S, self.oh * S), 0)
        ImageDraw.Draw(m).rounded_rectangle(
            [0, 0, self.ow * S - 1, self.oh * S - 1], radius=r * S, fill=255)
        return m.resize((self.ow, self.oh), Image.LANCZOS)

    # -- per-frame ---------------------------------------------------------
    def compose(self, content):
        if not self.enabled:
            return content
        canvas = self._image.copy()
        canvas.paste(content, (self.pad, self.pad), self.mask)
        return canvas

    def to_canvas(self, x, y):
        """Content-space -> canvas-space (overlays may live on the card)."""
        return x + self.pad, y + self.pad


def _float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
