"""styles.py — contact sheets for the built-in cursor and keycap libraries.

The kit ships 13 vector cursor shapes and a named keycap palette, and both are
regenerable at any size (the cursors are drawn, not bitmaps). That makes "which
style should this recording use" a five-second decision instead of a render.

    rec styles                 # writes sheet.png under the data home
    rec styles --out mine.png  # or wherever
"""

import json
import os

from PIL import Image, ImageDraw, ImageFont

from .layers import cursor as cursor_layer
from .layers import keycap as keycap_layer

CURSOR_STYLES = [
    "arrow", "text", "pointer", "crosshair", "open-hand", "closed-hand",
    "resize-ew", "resize-ns", "move", "cell", "zoom-in", "zoom-out",
    "not-allowed",
]
SAMPLE_TEXT = "Agent \u5f55\u5c4f \u21a9"
PANEL_KEYS = [
    {"t": 0.0, "label": "\u21e7", "key": "\u21e7", "word": "", "kind": "special"},
    {"t": 0.1, "label": "T", "key": "T", "word": "", "kind": "char"},
    {"t": 0.2, "label": "\u2423", "key": "\u2423", "word": "space", "kind": "special"},
    {"t": 0.3, "label": "\u23ce", "key": "\u23ce", "word": "return", "kind": "special"},
]
SAMPLE_KEY = "N E N G \u2423 \u23ce \u21e5"

_FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _font(px):
    for p in _FONT_CANDIDATES:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, px)
        except Exception:
            continue
    return ImageFont.load_default()


def load_presets(root):
    path = os.path.join(root, "themes", "keycaps.json")
    try:
        with open(path) as f:
            return json.load(f).get("presets") or {}
    except Exception:
        return {}


def _label(d, xy, text, font, fill):
    d.text(xy, text, font=font, fill=fill)


def build_sheet(presets, out, scale=2):
    """One PNG: cursor shapes on both background values, then keycap presets."""
    cell = 132 * scale
    pad = 26 * scale
    cols = 7
    rows = (len(CURSOR_STYLES) + cols - 1) // cols
    slice_h = cell * 2 + pad
    head = 58 * scale
    preset_rows = max(1, len(presets))
    preset_h = 84 * scale
    W = cols * cell + pad * 2
    H = head * 2 + rows * slice_h + preset_rows * preset_h + pad * 2 + head

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f_head = _font(22 * scale)
    f_name = _font(12 * scale)

    y = pad
    _label(d, (pad, y), "cursor styles  \u00b7  dark / light", f_head, (24, 24, 28))
    y += head

    # dark band + light band, so each shape is judged on both
    d.rectangle([0, y, W, y + cell], fill=(15, 17, 21))
    d.rectangle([0, y + cell, W, y + cell * 2], fill=(247, 247, 249))

    for i, style in enumerate(CURSOR_STYLES):
        cx = pad + (i % cols) * cell + cell // 2
        cy = y + (i // cols) * slice_h
        sprite, hx, hy = cursor_layer.build(style, int(74 * scale), True)
        img.paste(sprite, (int(cx - hx), int(cy + cell // 2 - hy)), sprite)
        sprite2, hx2, hy2 = cursor_layer.build(style, int(74 * scale), False)
        img.paste(sprite2, (int(cx - hx2), int(cy + cell + cell // 2 - hy2)),
                  sprite2)
        tw = d.textlength(style, font=f_name)
        _label(d, (cx - tw / 2, cy + cell * 2 + 4 * scale), style, f_name,
               (150, 150, 158))

    y += rows * slice_h + pad
    _label(d, (pad, y), "keycap styles  \u00b7  each row is one drawing, shown in two colourways",
           f_head, (24, 24, 28))
    y += head

    fk = _font(15 * scale)
    for name, cfg in presets.items():
        c = keycap_layer.cfg(cfg)
        if c.get("mode") == "panel":
            # a translucent strip with key names -- show it on both backdrops
            samples = []
            samp = dict(cfg)          # the sheet canvas is small: scale the type up
            samp["size"] = 0.17
            hh = 108 * scale
            for bg in ((28, 34, 44), (246, 246, 248)):
                band = Image.new("RGB", (330 * scale, hh), bg)
                keycap_layer.draw(band, PANEL_KEYS, 0.35, samp, hh, 330 * scale)
                samples.append(band)
            wdt = sum(b.width for b in samples) + 8 * scale
            hgt = max(b.height for b in samples)
            sp = Image.new("RGBA", (wdt, hgt), (0, 0, 0, 255))
            x = 0
            for b in samples:
                sp.paste(b, (x, (hgt - b.height) // 2))
                x += b.width + 8 * scale
        elif c.get("mode") == "caps":
            # Show the SAME drawing twice, in two colourways: a style is a shape,
            # colour is an option inside it.
            dark = cfg
            light = dict(cfg)
            light["fill"] = [236, 236, 240, 255]
            light["fill2"] = [198, 198, 206, 255]
            light["text"] = [34, 34, 38, 255]
            keys = ["\u21e7", "T", "\u2423", "\u23ce"]
            gap = 6 * scale
            strips = []
            for scheme in (dark, light):
                caps = [keycap_layer.cap(k, 22 * scale, scheme) for k in keys]
                wdt = int(sum(k.width for k in caps) + gap * (len(caps) - 1))
                hgt = max(k.height for k in caps)
                strip = Image.new("RGBA", (wdt, hgt), (0, 0, 0, 0))
                x = 0
                for k in caps:
                    strip.paste(k, (x, 0), k)
                    x += k.width + gap
                strips.append(strip)
            # each colourway sits on the backdrop it is meant for, so the row
            # reads as "same shape, two treatments"
            pads = 14 * scale
            half = [st.width + pads * 2 for st in strips]
            wdt = sum(half)
            hgt = max(st.height for st in strips) + pads * 2
            sp = Image.new("RGBA", (wdt, hgt), (0, 0, 0, 255))
            bands = [(24, 30, 40), (247, 247, 249)]
            x = 0
            for i, st in enumerate(strips):
                b = Image.new("RGB", (half[i], hgt), bands[i % 2])
                sp.paste(b, (x, 0))
                sp.paste(st, (x + pads, pads), st)
                x += half[i]
        else:
            sample = SAMPLE_KEY if c.get("view") == "key" else SAMPLE_TEXT
            key = ("sheet", name, sample, 40 * scale)
            sp = keycap_layer._CAPS.get(key)
            if sp is None:
                sp = keycap_layer._LINE(sample, 40 * scale, c)
                keycap_layer._CAPS[key] = sp
        tw = d.textlength(name, font=fk)
        _label(d, (pad, y + (preset_h - 40 * scale) // 2), name, fk, (90, 90, 96))
        band_top = y + 8 * scale
        band_h = preset_h - 16 * scale
        # the swatch has to contrast with the preset's own text colour, or a
        # white-on-glass style vanishes against a white sheet
        ink = cfg.get("text") or [32, 32, 36, 255]
        lum = (0.299 * ink[0] + 0.587 * ink[1] + 0.114 * ink[2])
        band = ((250, 250, 252) if lum < 140 else (24, 26, 32))
        # a caps row carries its own two backdrops, so the band stops there
        right = W - pad
        if c.get("mode") == "caps":
            right = min(right, pad + tw + 24 * scale + sp.width)
        d.rounded_rectangle([pad + tw + 24 * scale, band_top,
                             right, band_top + band_h],
                            radius=10 * scale, fill=band)
        img.paste(sp, (int(pad + tw + 40 * scale),
                       int(band_top + (band_h - sp.height) / 2)), sp)
        y += preset_h

    img.save(out)
    return out
