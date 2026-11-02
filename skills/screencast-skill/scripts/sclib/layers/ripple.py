"""Ripple layer: click feedback.

Two-tone rings (dark outer + white inner) so the effect stays visible on both
light and dark UI. Supersampled for antialiasing.

    "ripple": {
      "duration": 0.62, "radius_from": 14, "radius_to": 66,
      "thickness": 3, "dark": [28,28,34], "light": [255,255,255],
      "dark_alpha": 0.55, "press_scale": 0.88, "press_time": 0.18
    }
"""

from PIL import Image, ImageDraw

from .base import SS, ease_out_cubic

DEFAULTS = {
    "duration": 0.62,
    "radius_from": 14.0,
    "radius_to": 66.0,
    "thickness": 3.0,
    "dark": [28, 28, 34],
    "light": [255, 255, 255],
    "dark_alpha": 0.55,
    "press_scale": 0.88,
    "press_time": 0.18,
}


def cfg(style):
    c = dict(DEFAULTS)
    c.update(style or {})
    return c


def ring(dst, cx, cy, r, thickness, color, alpha):
    if r <= 0.5 or alpha <= 0.004:
        return
    pad = r + thickness + 3
    w = h = int(pad * 2)
    if w < 2:
        return
    tile = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    rr = r * SS
    tw = max(1, int(thickness * SS))
    ccx, ccy = w * SS / 2.0, h * SS / 2.0
    d.ellipse([ccx - rr, ccy - rr, ccx + rr, ccy + rr],
              outline=tuple(color) + (int(255 * min(alpha, 1.0)),), width=tw)
    tile = tile.resize((w, h), Image.LANCZOS)
    dst.paste(tile, (int(cx - pad), int(cy - pad)), tile)


def draw(dst, cx, cy, age, scale_ref, style=None):
    """Draw the ripple for a click that happened `age` seconds ago."""
    c = cfg(style)
    dur = float(c["duration"])
    if age < 0 or age > dur:
        return
    p = age / dur
    r = (float(c["radius_from"]) + (float(c["radius_to"]) - float(c["radius_from"]))
         * ease_out_cubic(p)) * scale_ref
    a = (1 - p) ** 1.6
    th = max(2, int(float(c["thickness"]) * scale_ref))
    ring(dst, cx, cy, r, th + max(1, int(scale_ref)),
         tuple(c["dark"]), a * float(c["dark_alpha"]))
    ring(dst, cx, cy, r - 2.5 * scale_ref, th, tuple(c["light"]), a)
    if p < 0.45:
        ring(dst, cx, cy, (10 - 8 * p) * scale_ref, th,
             tuple(c["light"]), (1 - p / 0.45) * 0.9)


def press_factor(clicks, now, style=None):
    """Cursor shrink factor while a button is held / just released."""
    c = cfg(style)
    pt = float(c["press_time"])
    ps = float(c["press_scale"])
    f = 1.0
    for cl in clicks:
        d = now - cl["t"]
        if 0 <= d < pt:
            f = min(f, ps + (1 - ps) * (d / pt))
    return f
