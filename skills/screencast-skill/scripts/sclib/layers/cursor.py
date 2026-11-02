"""Cursor layer: re-drawn pointer sprites.

The OS cursor is excluded at capture time (avfoundation -capture_cursor 0), so
this layer is the *only* pointer in the output. That is what makes smoothing,
scaling, press animation and style changes possible at all.

Styles mirror the set Recordly tracks (arrow / text / pointer / crosshair /
open-hand / closed-hand / resize-ew / resize-ns / not-allowed), so a plan that
records cursorType can switch sprites without further work.

Sprites are cached by (style, height, dark) — heights are quantised, otherwise
every zoom level would rebuild the sprite.
"""

from PIL import Image, ImageDraw, ImageFilter

from .base import SS

_CACHE = {}


def _arrow(W, H, fill, outline, S):
    pts = [(0.00, 0.00), (0.00, 0.735), (0.185, 0.596), (0.340, 0.960),
           (0.470, 0.896), (0.315, 0.548), (0.545, 0.548)]
    poly = [(x * W * 0.98, y * H * 0.98) for x, y in pts]
    d = ImageDraw.Draw(_canvas(W, H))
    return poly, d


def _canvas(W, H):
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _build_shape(style, W, H, fill, outline, S):
    """Draw the silhouette; returns (img, hotspot)."""
    img = _canvas(W, H)
    d = ImageDraw.Draw(img)
    lw = max(1, int(S * 1.4))

    if style == "arrow":
        pts = [(0.00, 0.00), (0.00, 0.735), (0.185, 0.596), (0.340, 0.960),
               (0.470, 0.896), (0.315, 0.548), (0.545, 0.548)]
        poly = [(x * W * 0.98, y * H * 0.98) for x, y in pts]
        d.line(poly + [poly[0]], fill=outline, width=int(S * 1.7), joint="curve")
        d.polygon(poly, fill=fill)
        return img, (0, 0)

    if style == "text":                       # I-beam
        cx = W / 2
        top, bot = H * 0.06, H * 0.94
        d.line([(cx, top), (cx, bot)], fill=fill, width=max(2, int(S * 2.2)))
        for y in (top, bot):
            d.line([(W * 0.18, y), (W * 0.82, y)], fill=fill,
                   width=max(2, int(S * 2.2)))
        d.line([(cx, top), (cx, bot)], fill=outline, width=max(1, lw))
        return img, (cx, H / 2)

    if style == "pointer":                    # pointing hand
        # palm
        d.rounded_rectangle([W * 0.22, H * 0.42, W * 0.86, H * 0.96],
                            radius=int(W * 0.20), fill=fill, outline=outline, width=lw)
        # index finger
        d.rounded_rectangle([W * 0.26, H * 0.06, W * 0.50, H * 0.60],
                            radius=int(W * 0.12), fill=fill, outline=outline, width=lw)
        return img, (W * 0.38, H * 0.06)

    if style == "crosshair":
        cx, cy = W / 2, H / 2
        w = max(2, int(S * 1.8))
        d.line([(cx, 0), (cx, H)], fill=fill, width=w)
        d.line([(0, cy), (W, cy)], fill=fill, width=w)
        d.ellipse([cx - W * 0.10, cy - W * 0.10, cx + W * 0.10, cy + W * 0.10],
                  outline=outline, width=lw)
        return img, (cx, cy)

    if style == "not-allowed":
        d.ellipse([2, 2, W - 2, H - 2], fill=fill, outline=outline, width=lw)
        d.line([(W * 0.22, H * 0.22), (W * 0.78, H * 0.78)],
               fill=outline, width=max(2, int(S * 1.8)))
        return img, (W / 2, H / 2)

    if style in ("open-hand", "closed-hand"):
        d.rounded_rectangle([W * 0.14, H * 0.20, W * 0.86, H * 0.92],
                            radius=int(W * 0.22), fill=fill, outline=outline, width=lw)
        if style == "open-hand":
            for i in range(3):
                x = W * (0.22 + i * 0.20)
                d.rounded_rectangle([x, H * 0.04, x + W * 0.14, H * 0.34],
                                    radius=int(W * 0.07), fill=fill,
                                    outline=outline, width=lw)
        return img, (W / 2, H * 0.30)

    if style in ("resize-ew", "resize-ns"):
        horiz = style == "resize-ew"
        a = [(0.06, 0.5), (0.34, 0.22), (0.34, 0.78)] if horiz else \
            [(0.5, 0.06), (0.22, 0.34), (0.78, 0.34)]
        b = [(0.94, 0.5), (0.66, 0.22), (0.66, 0.78)] if horiz else \
            [(0.5, 0.94), (0.22, 0.66), (0.78, 0.66)]
        d.polygon([(x * W, y * H) for x, y in a], fill=fill, outline=outline)
        d.polygon([(x * W, y * H) for x, y in b], fill=fill, outline=outline)
        return img, (W / 2, H / 2)

    if style == "move":                      # four-way drag cursor
        cx, cy = W / 2, H / 2
        ln = max(2, int(S * 1.9))
        d.line([(cx, H * 0.06), (cx, H * 0.94)], fill=fill, width=ln)
        d.line([(W * 0.06, cy), (W * 0.94, cy)], fill=fill, width=ln)
        for pts in (
            [(0.06, 0.5), (0.26, 0.34), (0.26, 0.66)],
            [(0.94, 0.5), (0.74, 0.34), (0.74, 0.66)],
            [(0.5, 0.06), (0.34, 0.26), (0.66, 0.26)],
            [(0.5, 0.94), (0.34, 0.74), (0.66, 0.74)],
        ):
            d.polygon([(x * W, y * H) for x, y in pts], fill=fill)
        return img, (cx, cy)

    if style == "cell":                      # spreadsheet cell: boxed crosshair
        cx, cy = W / 2, H / 2
        b = W * 0.30
        w = max(2, int(S * 1.7))
        d.rectangle([cx - b, cy - b, cx + b, cy + b], outline=fill, width=w)
        d.line([(cx, 0), (cx, cy - b)], fill=fill, width=w)
        d.line([(cx, cy + b), (cx, H)], fill=fill, width=w)
        d.line([(0, cy), (cx - b, cy)], fill=fill, width=w)
        d.line([(cx + b, cy), (W, cy)], fill=fill, width=w)
        return img, (cx, cy)

    if style in ("zoom-in", "zoom-out"):     # magnifier, +/- in the lens
        cx, cy = W * 0.44, H * 0.42
        r = W * 0.36
        w = max(2, int(S * 1.9))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill,
                  outline=outline, width=w)
        d.line([(cx + r * 0.68, cy + r * 0.68), (W * 0.97, H * 0.97)],
               fill=fill, width=int(S * 2.6))
        ink = outline
        d.line([(cx - r * 0.5, cy), (cx + r * 0.5, cy)], fill=ink, width=w)
        if style == "zoom-in":
            d.line([(cx, cy - r * 0.5), (cx, cy + r * 0.5)], fill=ink, width=w)
        return img, (cx, cy)

    # unknown style -> arrow
    return _build_shape("arrow", W, H, fill, outline, S)


def _load_asset(spec, height):
    """Load a user-supplied cursor image.

    spec: "assets/arrow.png"  or  {"path": "...", "hotspot": [fx, fy]}
    hotspot is fractional (0..1) relative to the image, default top-left for
    arrows and centre for everything else.
    """
    import os
    from PIL import Image as _Image
    if isinstance(spec, dict):
        path = spec.get("path")
        hot = spec.get("hotspot")
    else:
        path, hot = spec, None
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), path)
    try:
        img = _Image.open(path).convert("RGBA")
    except Exception:
        return None
    if img.height <= 0:
        return None
    w = max(2, int(round(img.width * height / img.height)))
    img = img.resize((w, max(2, int(height))), _Image.LANCZOS)
    # Apple's cursor PDFs are the plain silhouette; the OS adds the white halo
    # at draw time. Without it a black glyph disappears on dark content.
    try:
        alpha = img.split()[3]
        halo = Image.new("RGBA", img.size, (255, 255, 255, 0))
        halo.putalpha(alpha.filter(ImageFilter.MaxFilter(5)))
        img = Image.alpha_composite(halo, img)
    except Exception:
        pass
    fx, fy = (hot if hot else (0.0, 0.0))
    return img, fx * img.width, fy * img.height


def build(style="arrow", height=40, dark=True, assets=None):
    """Return (RGBA sprite, hotspot_x, hotspot_y). Cached.

    `assets` maps a style name to an image (see theme cursor.assets); when a
    style has an asset it wins over the built-in vector drawing, so pointer art
    is fully replaceable without touching this module.
    """
    style = (style or "arrow").lower()
    if style in ("ibeam", "i-beam", "caret"):
        style = "text"
    if style == "hand":
        style = "pointer"
    h = max(8, int(round(height)))

    spec = (assets or {}).get(style)
    if spec is not None:
        akey = ("asset", repr(spec), h)
        if akey not in _CACHE:
            got = _load_asset(spec, h)
            if got is not None:
                _CACHE[akey] = got
        if akey in _CACHE:
            return _CACHE[akey]

    key = (style, h, bool(dark))
    if key in _CACHE:
        return _CACHE[key]

    S = 6
    H = int(h * S)
    wide = style in ("arrow", "move")
    W = int(H * (0.70 if wide else 0.62))
    fill = (22, 22, 26, 255) if dark else (250, 250, 252, 255)
    outline = (255, 255, 255, 255) if dark else (18, 18, 22, 255)

    shape, hot = _build_shape(style, W, H, fill, outline, S)

    img = _canvas(W, H)
    sh = _canvas(W, H)
    sh.paste(shape, (int(S * 1.2), int(S * 1.8)))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 1.1)))
    img.alpha_composite(shape)

    out = img.resize((max(2, int(W / S)), max(2, int(H / S))), Image.LANCZOS)
    hx, hy = hot[0] / S, hot[1] / S
    _CACHE[key] = (out, hx, hy)
    return _CACHE[key]
