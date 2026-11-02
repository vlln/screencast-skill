"""Keycap layer: an independent, fully themeable overlay.

This is deliberately its own layer — Recordly has no key overlay at all, and in
this kit the keycaps are composited in post, so the presentation is a *style*
question, not a recording one.

Theme knobs (all optional):

    "keycap": {
      "position": "bottom-center",   # bottom-center | top-center | bottom-left | bottom-right
      "max_visible": 3,              # how many recent keys stay on screen
      "hold": 1.70,                  # seconds each cap stays
      "fade_in": 0.07, "fade_out": 0.35,
      "size": 0.062,                 # font size as a fraction of frame height
      "gap": 0.35,                   # gap as a fraction of font size
      "pad_x": 0.52, "pad_y": 0.34, "radius": 0.30,
      "fill": [24,24,28,235], "border": [255,255,255,52], "text": [248,248,252,255],
      "margin": 0.10                 # distance from the frame edge, fraction of height
    }
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .base import SS, paste_rgba

FONT_PATHS = [
    "/System/Library/Fonts/SFNS.ttf",        # has ⌘⌥⌃⇧ (verified)
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
# SFNS has no CJK glyphs and Pillow does no font fallback, so a Chinese
# keycap would render as a row of .notdef boxes without this second list.
FONT_PATHS_CJK = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]


def _needs_cjk(text):
    return any(ord(c) >= 0x2E80 for c in text or "")
_FONTS = {}
_CAPS = {}


# A line can mix glyph sets that no single macOS font covers: PingFang has the
# Chinese but no keycap symbols, SFNS has the symbols but no Chinese. Pillow does
# no fallback of its own, so a mixed line renders as tofu -- which is exactly how
# the space and return caps came out as boxes. Resolve per character instead.
FONT_FALLBACK = [
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/System/Library/Fonts/STIXTwoMath.otf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
_MISSING = "\ue0ff"          # private-use codepoint: never has a real glyph

DEFAULTS = {
    # "line"   : consecutive keys merge into one growing row; older rows fade out
    # "caps"   : one pill per key, last N visible (the original style)
    "mode": "line",
    # caps mode: Keyviz's four drawing styles plus the two colours they need
    "style": "lowprofile",     # minimal | laptop | lowprofile | pbt
    "fill2": None,             # base / secondary colour (defaults to a shade of fill)
    "border_width": 2,
    # "text" shows what the app received (Chinese renders as Chinese), "key"
    # shows the physical keys -- the pinyin letters behind those characters.
    "view": "text",
    "line_gap": 1.6,      # seconds of silence that ends a line
    "line_hold": 2.6,     # how long a finished line stays on screen
    "max_rows": 3,        # how many lines are stacked at once
    "max_chars": 32,      # truncate very long lines
    "position": "bottom-center",
    # where the whole block sits, and how much it holds back: an overlay that
    # covers the thing being demonstrated is a bug, so placement and opacity are
    # theme fields, not constants.
    "offset_x": 0.0,        # fraction of frame height, +/- nudge
    "offset_y": 0.0,
    "opacity": 1.0,
    "press_time": 0.14,     # seconds a cap stays visibly pressed
    "max_visible": 3,
    "hold": 1.70,
    "fade_in": 0.07,
    "fade_out": 0.35,
    "size": 0.062,
    "gap": 0.35,
    "pad_x": 0.52,
    "pad_y": 0.34,
    "radius": 0.30,
    "fill": [24, 24, 28, 235],
    "border": [255, 255, 255, 52],
    "text": [248, 248, 252, 255],
    "margin": 0.12,
}


def _load(path, px):
    import os
    key = (path, int(px))
    if key in _FONTS:
        return _FONTS[key]
    f = None
    try:
        if os.path.exists(path):
            f = ImageFont.truetype(path, px)
    except Exception:
        f = None
    _FONTS[key] = f
    return f


def _signature(font, ch):
    im = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(im).text((2, 0), ch, font=font, fill=255)
    return im.tobytes()


def _has_glyph(font, ch):
    if ch == " ":
        return True
    key = ("nd", id(font))
    nd = _FONTS.get(key)
    if nd is None:
        nd = _signature(font, _MISSING)
        _FONTS[key] = nd
    return _signature(font, ch) != nd


def _resolve(ch, px):
    """The first font that can actually draw `ch`, preferring the text font."""
    cjk = ord(ch) >= 0x2E80
    order = (FONT_PATHS_CJK + FONT_PATHS + FONT_FALLBACK) if cjk else \
            (FONT_PATHS + FONT_FALLBACK + FONT_PATHS_CJK)
    key = ("res", ch, int(px))
    if key in _FONTS:
        return _FONTS[key]
    for path in order:
        f = _load(path, px)
        if f is not None and _has_glyph(f, ch):
            _FONTS[key] = f
            return f
    f = _load(FONT_PATHS[0], px) or ImageFont.load_default()
    _FONTS[key] = f
    return f


def _font(px, cjk=False):
    """Base font for metrics only; drawing goes through _paint."""
    px = max(8, int(px))
    key = ("base", bool(cjk), px)
    if key in _FONTS:
        return _FONTS[key]
    order = (FONT_PATHS_CJK if cjk else FONT_PATHS) + FONT_FALLBACK
    for p in order:
        f = _load(p, px)
        if f is not None:
            _FONTS[key] = f
            return f
    _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


SPACE_CH = "\u2423"
SPACE_W = 0.66              # width of the drawn space glyph, in font sizes


def _space_box(d, x, baseline, px, fill):
    """Draw the space symbol ourselves.

    Apple Symbols does have U+2423, but it is a tiny box parked on the baseline,
    which reads as a rendering bug next to full-height letters. A space bar wants
    a wide, centred outline instead.
    """
    w = px * SPACE_W * 0.62
    h = px * 0.30
    y0 = baseline - px * 0.42
    d.rounded_rectangle([x, y0, x + w, y0 + h], radius=px * 0.07,
                        outline=fill, width=max(1, int(px * 0.085)))


FONT_MONO = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
]


def _font_mono(px):
    px = max(8, int(px))
    key = ("mono", px)
    if key in _FONTS:
        return _FONTS[key]
    for path in FONT_MONO:
        f = _load(path, px)
        if f is not None:
            _FONTS[key] = f
            return f
    _FONTS[key] = _font(px)
    return _FONTS[key]


def _glyph_scale(ch):
    """Symbol glyphs are drawn small by their fonts; scale them up to match.

    SF Pro's cap height versus, say, U+21A9 in Apple Symbols are nothing alike,
    which is why "Agent ↩" looked like a word followed by a speck.
    """
    o = ord(ch)
    if 0x2190 <= o <= 0x21FF or 0x2300 <= o <= 0x23FF or 0x25A0 <= o <= 0x25FF:
        return 1.42
    if ch in "\u2303\u2325\u21e7\u2318\u232b\u2326\u238b":
        return 1.30
    return 1.0


def _measure(text, px):
    asc, desc = _font(px).getmetrics()
    w = 0.0
    for ch in text:
        if ch == SPACE_CH:
            w += px * SPACE_W
        else:
            w += _resolve(ch, px * _glyph_scale(ch)).getlength(ch)
    return w, asc + desc, asc


def _paint(d, x, baseline, text, px, fill):
    for ch in text:
        if ch == SPACE_CH:
            _space_box(d, x, baseline, px, fill)
            x += px * SPACE_W
            continue
        px_ch = px * _glyph_scale(ch)
        f = _resolve(ch, px_ch)
        if ch != " ":
            d.text((x, baseline), ch, font=f, fill=fill, anchor="ls")
        x += f.getlength(ch)


def cfg(style):
    c = dict(DEFAULTS)
    c.update(style or {})
    return c


def make(label, font_px, style=None):
    """A single keycap sprite, supersampled."""
    c = cfg(style)
    key = (label, int(font_px), tuple(c["fill"]), tuple(c["border"]),
           tuple(c["text"]), c["radius"])
    if key in _CAPS:
        return _CAPS[key]

    tw, th, _asc = _measure(label, font_px)
    w = max(int(font_px * 1.2), int(tw) + int(font_px * c["pad_x"]) * 2)
    h = int(th) + int(font_px * c["pad_y"]) * 2

    # same construction as a keycap (dark body, lit face, top highlight), just
    # stretched into a strip -- one material across the whole overlay
    base = tuple(c["fill"])
    body = _shade(base, 0.70)
    r = int(h * SS * c["radius"])
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], radius=r, fill=body[:3] + (base[3],))
    lip = max(2, int(h * SS * 0.045))
    inner = _round_mask(w * SS, h * SS - lip, max(2, int(r * 0.94)))
    face = _vgrad(w * SS, h * SS - lip, _shade(base, 1.24), _shade(base, 0.98))
    img.paste(face, (0, 0), inner)
    hl = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - lip - 1], radius=max(2, int(r * 0.94)),
        outline=tuple(c["border"][:3]) + (int(c["border"][3] * 0.9),),
        width=max(1, int(font_px * SS / 22)))
    top_mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(top_mask).rectangle(
        [0, 0, w * SS, int(h * SS * 0.45)], fill=255)
    img.alpha_composite(Image.composite(
        hl, Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0)), top_mask))
    tw_ss, th_ss, asc_ss = _measure(label, font_px * SS)
    _paint(d, (w * SS - tw_ss) / 2, (h * SS - th_ss) / 2 + asc_ss,
           label, font_px * SS, tuple(c["text"]))
    out = img.resize((w, h), Image.LANCZOS)
    _CAPS[key] = out
    return out


def _entry(item, view):
    """Normalise a plan key entry to (t, shown char, is_return)."""
    if isinstance(item, dict):
        t = item["t"]
        word = item.get("word") or ""
        shown = item.get("key") if view == "key" else item.get("label")
        if view != "key" and word == "space":
            shown = " "                      # a sentence needs a space, not a box
        return t, (shown or item.get("label") or "?"), word in ("return", "enter")
    t, label = item[0], item[1]
    raw = item[2] if len(item) > 2 else label
    return t, (raw or label), label in ("return", "enter")


# Physical width of a key, in cap-height units. A spacebar is a bar and Return
# is twice a letter: getting these proportions wrong is most of what makes a
# key overlay look like a row of identical buttons instead of a keyboard.
# Keyviz's four keycap styles, in its own metrics (all relative to the font size
# `s`): the cap body is 2.25-2.75 s tall, the label is `s` tall, and the corner
# radius is a 0..1 factor times 1.25 s. Matching the numbers matters more than
# matching the spirit -- "looks similar" is what produced the flat buttons.
STYLES = ("minimal", "laptop", "lowprofile", "pbt")

# Glyph over word, the way Keyviz renders modifiers and specials.
GLYPH_WORD = {
    "\u2423": ("\x00bar", "space"),
    "\u23ce": ("\u21a9", "return"),
    "\u21e5": ("\u21c6", "tab"),
    "\u232b": ("\u232b", "del"),
    "\u2326": ("\u2326", "del"),
    "\u238b": ("esc", ""),
    "\u21de": ("\u2912", "pg up"),
    "\u21df": ("\u2913", "pg dn"),
    "\u2196": ("\u21f1", "home"),
    "\u2198": ("\u21f2", "end"),
}
ARROWS = "\u2190\u2192\u2191\u2193"
MOD_GLYPHS = "\u2303\u2325\u21e7\u2318"


def _shade(rgb, k):
    """k > 1 lightens toward white, k < 1 darkens toward black."""
    if k >= 1.0:
        f = lambda v: int(v + (255 - v) * (k - 1.0))
    else:
        f = lambda v: int(v * k)
    return tuple(max(0, min(255, f(v))) for v in rgb[:3])


def _vgrad(w, h, c0, c1, diag=False):
    img = Image.linear_gradient("L")
    if diag:
        img = img.rotate(-45, expand=False)
    g = img.resize((w, h), Image.BILINEAR)
    return ImageOps.colorize(g, tuple(c0[:3]), tuple(c1[:3]))


def _round_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1],
                                        radius=max(1, int(r)), fill=255)
    return m


def _rr(size, radius_px, fill=None, outline=None, width=1):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=max(1, int(radius_px)),
        fill=fill, outline=outline, width=width)
    return layer


def cap(label, font_px, style=None, pressed=False):
    """One keycap in Keyviz's drawing model.

    Four styles, matching Keyviz's `minimal` / `laptop` / `lowprofile` / `pbt`:

      minimal     no body at all -- the glyph itself
      lowprofile  a lit top face sitting on a secondary-coloured base slab
      laptop      one face, subtle top-lit gradient, hairline inset highlight
      pbt         a thick base with a smaller face inset into it

    `fill` is the face colour, `fill2` the base, `radius` is 0..1 (times 1.25 s).
    """
    c = cfg(style)
    kind = str(c.get("style") or "lowprofile").lower()
    if kind not in STYLES:
        kind = "lowprofile"
    key = ("cap2", label, int(font_px), kind, bool(pressed), tuple(c["fill"]),
           tuple(c.get("fill2") or c["fill"]), tuple(c["border"]),
           tuple(c["text"]), c["radius"])
    if key in _CAPS:
        return _CAPS[key]

    s = float(font_px)
    S = 4
    radius = max(1.0, float(c["radius"]) * s * 1.25)
    face_col = tuple(c["fill"][:3])
    base_col = tuple((c.get("fill2") or _shade(face_col, 0.72))[:3])
    ink = tuple(c["text"])
    mod = label and (label[0] in MOD_GLYPHS or label in
                     ("\u2303", "\u2325", "\u21e7", "\u2318"))

    def text_w(ch, px):
        return _measure(ch, px)[0]

    # ---- content ---------------------------------------------------------
    gw = word = ""
    if label in GLYPH_WORD:
        gw, word = GLYPH_WORD[label]
    elif label and label[0] in ARROWS:
        gw = label
    elif mod and len(label) > 1:
        gw, word = label[0], label[1:]
    elif mod:
        gw = label
    else:
        word = label or ""

    pad_in = s * (0.5 if float(c["radius"]) < 0.75 else
                  0.5 + float(c["radius"]) - 0.75)
    pad_blk = s * 0.4

    # every style is 2.25 s wide unless it is a modifier (2.5 s), pbt a step up
    min_w = s * ((3.0 if mod else 2.75) if kind == "pbt" else
                 (2.5 if mod else 2.25))
    inner = 0.0
    if gw and word:
        inner = max(text_w(gw, s * 0.6), text_w(word, s * 0.46))
    elif word:
        inner = text_w(word, s)
    elif gw:
        inner = text_w(gw, s * 0.8)
    body_w = max(min_w, inner + 2 * pad_in)

    if kind == "minimal":
        h = s * 1.9
        W, H = int(inner + s * 0.6) * S, int(h) * S
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        _stack_text(d, (W - inner * S) / 2.0, H, gw, word, s * S, ink,
                    0.6 if word else 0.8)
        out = img.resize((int(W / S), int(H / S)), Image.LANCZOS)
        _CAPS[key] = out
        return out

    if kind == "lowprofile":
        h_face, h_total = s * 2.25, s * 2.5
    elif kind == "laptop":
        h_face, h_total = s * 2.25, s * 2.25
    else:                                            # pbt
        h_face, h_total = s * 2.2, s * 2.75
        body_w = body_w + s * 0.6                    # outer marginInline 0.3
    W, H = int(body_w) * S, int(h_total) * S
    FW, FH = int(body_w) * S, int(h_face) * S

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # Pressed: the face drops toward the base and picks up an inset shadow along
    # its top edge. Ported from Keyviz, which animates `y: size * 0.25`.
    press_px = int(s * (0.22 if kind == "lowprofile" else 0.18) * S) if pressed else 0
    border_px = max(1, int(float(c.get("border_width", 2)) * S * 0.5))
    ring = tuple(c["border"][:3])

    if kind == "lowprofile":
        # base slab, then the face on top of it
        base = _rr((W, FH), radius * S, fill=base_col + (255,),
                   outline=ring + (255,) if c["border"][3] > 0 else None,
                   width=border_px)
        img.alpha_composite(base, (0, H - FH))
        face = _rr((W, FH), radius * S, fill=face_col + (255,),
                   outline=ring + (255,) if c["border"][3] > 0 else None,
                   width=border_px)
        img.alpha_composite(face, (0, press_px))

    elif kind == "laptop":
        # Keyviz: one face, a top-lit gradient, a soft INSET highlight along the
        # top edge, and a hard little shadow under it. The shadow is what makes
        # the key sit on the surface instead of floating.
        H = FH + int(s * 0.16 * S)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow = _rr((W, FH), radius * S, fill=(0, 0, 0, 128))
        shadow = shadow.filter(ImageFilter.GaussianBlur(S * 1.1))
        img.alpha_composite(shadow, (0, int(s * 0.1 * S)))

        face = Image.new("RGBA", (W, FH), (0, 0, 0, 0))
        grad = _vgrad(W, FH, _shade(face_col, 1.30), face_col)
        face.paste(grad, (0, 0), _rr((W, FH), radius * S, fill=(255, 255, 255, 255)))
        hl = _rr((W, FH), radius * S, outline=_shade(face_col, 1.75) + (170,),
                 width=max(1, int(S * 1.1)))
        topmask = Image.new("L", (W, FH), 0)
        ImageDraw.Draw(topmask).rectangle([0, 0, W, int(FH * 0.30)], fill=255)
        face.alpha_composite(Image.composite(
            hl, Image.new("RGBA", (W, FH), (0, 0, 0, 0)), topmask))
        if c["border"][3] > 0:
            face.alpha_composite(_rr((W, FH), radius * S, outline=ring + (255,),
                                     width=border_px))
        img.alpha_composite(face, (0, press_px))

    else:                                            # pbt
        base = _vgrad(W, H, _shade(base_col, 1.15), _shade(base_col, 0.82))
        img.paste(base, (0, 0), _rr((W, H), radius * S, fill=(255, 255, 255, 255)))
        if c["border"][3] > 0:
            img.alpha_composite(_rr((W, H), radius * S, outline=ring + (255,),
                                    width=border_px))
        mx, my = int(s * 0.3 * S), 0
        fw, fh = W - 2 * mx, FH
        face = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        grad = _vgrad(fw, fh, _shade(face_col, 1.12), _shade(face_col, 0.94))
        face.paste(grad, (0, 0), _rr((fw, fh), radius * S, fill=(255, 255, 255, 255)))
        # the thin bottom edge that reads as a PBT profile
        ImageDraw.Draw(face).rounded_rectangle(
            [0, 0, fw - 1, fh - 1], radius=max(1, int(radius * S)),
            outline=_shade(face_col, 0.65) + (255,),
            width=max(1, int(s * 0.06 * S)))
        img.alpha_composite(face, (mx, my + press_px))

    if press_px:
        shade = _rr((W, FH), radius * S,
                    outline=(0, 0, 0, 150), width=max(1, int(S * 1.2)))
        m = Image.new("L", (W, FH), 0)
        ImageDraw.Draw(m).rectangle([0, press_px, W, press_px + int(FH * 0.34)],
                                    fill=255)
        img.alpha_composite(Image.composite(
            shade, Image.new("RGBA", (W, FH), (0, 0, 0, 0)), m), (0, 0))

    d = ImageDraw.Draw(img)
    _stack_text(d, (W - inner * S) / 2.0, FH + press_px, gw, word, s * S, ink,
                0.6 if word else 0.8)

    out = img.resize((int(W / S), int(H / S)), Image.LANCZOS)
    _CAPS[key] = out
    return out


BAR = "\x00bar"


def _bar(d, cx, cy, width, height, ink):
    """The spacebar icon: a wide low bar. Unambiguous at any size."""
    x0, y0 = cx - width / 2.0, cy - height / 2.0
    d.rounded_rectangle([x0, y0, x0 + width, y0 + height],
                        radius=height * 0.45, fill=ink)


def _stack_text(d, x, h, glyph, word, s_ss, ink, glyph_scale):
    """Glyph over word for modifiers/specials; a single centred glyph otherwise."""
    if not glyph and not word:
        return
    if glyph == BAR:
        # spacebar icon above its name
        wsz = s_ss * 0.46
        _w, wh, _wa = _measure(word, wsz) if word else (0, 0, 0)
        bar_h = s_ss * 0.17
        bar_w = s_ss * 0.92
        total = bar_h + wh * 0.55
        top = (h - total) / 2.0
        _bar(d, x + s_ss * 0.55, top + bar_h / 2.0, bar_w, bar_h, ink)
        if word:
            ww, _w2, _wa2 = _measure(word, wsz)
            _paint(d, x + (s_ss * 1.1 - ww) / 2.0, top + bar_h + wh * 0.55,
                   word, wsz, ink)
        return
    if glyph and word:
        gsz = s_ss * glyph_scale
        wsz = s_ss * 0.46
        gw, gh, ga = _measure(glyph, gsz)
        ww, wh, _wa = _measure(word, wsz)
        total = gh + wh * 0.55
        top = (h - total) / 2.0
        _paint(d, x + (s_ss * 1.1 - gw) / 2.0, top + ga, glyph, gsz, ink)
        _paint(d, x + (s_ss * 1.1 - ww) / 2.0, top + gh + wh * 0.55, word, wsz, ink)
    else:
        ch = word or glyph
        px = s_ss * (1.0 if word else glyph_scale)
        tw, th, ta = _measure(ch, px)
        _paint(d, x + (s_ss * 1.1 - tw) / 2.0, (h - th) / 2.0 + ta, ch, px, ink)


POSITIONS = ("bottom-center", "bottom-left", "bottom-right",
             "top-center", "top-left", "top-right")


def _place(c, frame_w, frame_h, total_w, total_h):
    """Block anchor for any `position`, plus the offset nudge."""
    margin = int(frame_h * float(c["margin"]))
    pos = str(c.get("position") or "bottom-center")
    if pos not in POSITIONS:
        pos = "bottom-center"
    vert, _, horz = pos.partition("-")
    if horz == "left":
        x = margin
    elif horz == "right":
        x = frame_w - margin - total_w
    else:
        x = (frame_w - total_w) // 2
    y = margin if vert == "top" else frame_h - margin - total_h
    x += int(frame_h * float(c.get("offset_x") or 0.0))
    y += int(frame_h * float(c.get("offset_y") or 0.0))
    return x, y


def build_lines(keys, line_gap=1.6, view="text"):
    """Merge consecutive keystrokes into rows.

    A "line" ends after `line_gap` seconds of silence (so one word stays one
    row), or immediately on Return. This is what makes the overlay read like
    typing rather than like a stream of isolated keycaps.
    """
    lines = []
    for item in keys:
        t, ch, is_break = _entry(item, view)
        if is_break:
            if lines:                       # close the current row, show the ⏎
                lines[-1]["pieces"].append((t, "\u23ce"))
                lines[-1]["t_last"] = t
                lines.append({"t0": t, "t_last": t, "pieces": [],
                              "break_after": True})
            continue                        # a leading break draws nothing
        if lines and (t - lines[-1]["t_last"]) <= line_gap:
            lines[-1]["pieces"].append((t, ch))
            lines[-1]["t_last"] = t
            lines[-1].pop("break_after", None)
        else:
            lines.append({"t0": t, "t_last": t, "pieces": [(t, ch)]})
    return lines


def _draw_lines(dst, keys, now, c, frame_h, frame_w):
    hold = float(c["line_hold"])
    gap = float(c["line_gap"])
    rows = []
    for ln in build_lines(keys, gap, c["view"]):
        if now < ln["t0"]:
            continue
        end = ln["t_last"]
        if now > end + hold:
            continue
        text = "".join(ch for t, ch in ln["pieces"] if t <= now)[:int(c["max_chars"])]
        if not text:
            continue
        # A finished row holds solid for most of its life and only fades at the
        # very end. Fading linearly from the moment typing stops made the caption
        # of a beat already ghostly while that beat was still playing.
        age_after_end = now - end
        solid = min(0.15 + hold * 0.55, hold)
        alpha = 1.0 if age_after_end <= solid else max(
            0.0, 1.0 - (age_after_end - solid) / max(1e-3, hold - solid))
        rows.append((text, alpha, ln["t_last"], now - ln["t_last"] < 0.35))
    if not rows:
        return

    rows = rows[-int(c["max_rows"]):]
    font_px = max(18, int(frame_h * float(c["size"])))
    gap_px = int(font_px * float(c["gap"]))
    margin = int(frame_h * float(c["margin"]))

    sprites = []
    for text, alpha, _t, fresh in rows:
        # the sprite depends on the whole visual config, not just the text: the
        # demo switches keycap styles mid-take, and a text-only key would keep
        # serving the first style's pill
        key = ("line", text, font_px,
               tuple(c["fill"]), tuple(c["border"]), tuple(c["text"]),
               c["radius"], c["pad_x"], c["pad_y"])
        sp = _CAPS.get(key)
        if sp is None:
            sp = _LINE(text, font_px, c)
            _CAPS[key] = sp
        pop = 1.06 if fresh else 1.0
        sprites.append((sp, alpha, pop))

    op = max(0.0, min(1.0, float(c.get("opacity", 1.0))))
    widths = [int(sp.width * pop) for sp, _, pop in sprites]
    heights = [sp.height for sp, _, _ in sprites]
    total_w = max(widths)
    total_h = sum(heights) + gap_px * (len(sprites) - 1)
    bx, y = _place(c, frame_w, frame_h, total_w, total_h)

    for (sp, alpha, pop), wdt, hgt in zip(sprites, widths, heights):
        x = bx + (total_w - wdt) // 2
        s2 = sp.resize((max(2, wdt), max(2, hgt)), Image.LANCZOS)
        paste_rgba(dst, s2, x, y, alpha * op)
        y += hgt + gap_px


def _LINE(text, font_px, c):
    """One row of typing: a single rounded pill holding the accumulated line."""
    text = text.replace("\u2423", " ").replace("\u23ce", "\u21a9")
    tw, th, _asc = _measure(text, font_px)
    w = int(tw) + int(font_px * c["pad_x"]) * 2
    h = int(th) + int(font_px * c["pad_y"]) * 2
    # same construction as a keycap (dark body, lit face, top highlight), just
    # stretched into a strip -- one material across the whole overlay
    base = tuple(c["fill"])
    body = _shade(base, 0.70)
    r = int(h * SS * c["radius"])
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], radius=r, fill=body[:3] + (base[3],))
    lip = max(2, int(h * SS * 0.045))
    inner = _round_mask(w * SS, h * SS - lip, max(2, int(r * 0.94)))
    face = _vgrad(w * SS, h * SS - lip, _shade(base, 1.24), _shade(base, 0.98))
    img.paste(face, (0, 0), inner)
    hl = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - lip - 1], radius=max(2, int(r * 0.94)),
        outline=tuple(c["border"][:3]) + (int(c["border"][3] * 0.9),),
        width=max(1, int(font_px * SS / 22)))
    top_mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(top_mask).rectangle(
        [0, 0, w * SS, int(h * SS * 0.45)], fill=255)
    img.alpha_composite(Image.composite(
        hl, Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0)), top_mask))
    tw_ss, th_ss, asc_ss = _measure(text, font_px * SS)
    _paint(ImageDraw.Draw(img), (w * SS - tw_ss) / 2,
           (h * SS - lip - th_ss) / 2 + asc_ss,
           text, font_px * SS, tuple(c["text"]))
    return img.resize((w, h), Image.LANCZOS)


def _draw_panel(dst, keys, now, c, frame_h, frame_w):
    """ShowMeTheKey's style: no caps at all.

    One translucent strip, keys printed side by side in monospace. Their
    `smtk-keys-area.c` computes the row from a string height (frame * 0.8), a
    font at 0.9 of that, and a margin of `string_height * margin_ratio`; the row
    is centred or right-aligned as a whole. There is no per-key artwork, and
    that is the point -- this is the quiet end of the range.
    """
    hold = float(c.get("hold", 1.7))
    show_ime = bool(c.get("show_ime", False))
    labels = []
    for k in keys:
        if not show_ime and isinstance(k, dict) and k.get("kind") == "ime":
            continue
        ent = _entry(k, c["view"])
        age = now - ent[0]
        if 0 <= age <= hold:
            # ShowMeTheKey prints key *names*, not symbols: "SPACE", not a box
            word = (k.get("word") or "").strip() if isinstance(k, dict) else ""
            labels.append((age, word.upper() if word else ent[1]))
    labels = labels[-int(c.get("max_visible", 5)):]
    if not labels:
        return

    font_px = max(14, int(frame_h * float(c.get("size", 0.06)) * 1.25))
    gap = font_px * 0.9
    text = " ".join(ch for _a, ch in labels)
    tw, th, asc = _measure_mono(text, font_px)
    ph = int(font_px * 1.9)
    padx = int(font_px * 0.85)
    pw = int(tw + gap * 0 + padx * 2)
    x, y = _place(c, frame_w, frame_h, pw, ph)

    op = max(0.0, min(1.0, float(c.get("opacity", 1.0))))
    # fade the whole strip in/out with the newest key, like a bezel
    newest = min(a for a, _ in labels)
    fade = min(1.0, newest / max(1e-3, float(c.get("fade_in", 0.10))))
    panel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    fill = tuple(c["fill"])
    ImageDraw.Draw(panel).rounded_rectangle(
        [0, 0, pw - 1, ph - 1], radius=int(ph * float(c.get("radius", 0.5))),
        fill=fill[:3] + (int(fill[3] if len(fill) > 3 else 255),))
    paste_rgba(dst, panel, x, y, fade * op)

    layer = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    _paint_mono(ImageDraw.Draw(layer), (pw - tw) / 2.0,
                (ph - th) / 2.0 + asc, text, font_px, tuple(c["text"]))
    paste_rgba(dst, layer, x, y, fade * op)


def _measure_mono(text, px):
    f = _font_mono(px)
    asc, desc = f.getmetrics()
    return f.getlength(text), asc + desc, asc


def _paint_mono(d, x, baseline, text, px, fill):
    d.text((x, baseline), text, font=_font_mono(px), fill=fill, anchor="ls")


def draw(dst, keys, now, style, frame_h, frame_w):
    """Composite the active keycaps.

    keys: [(t_down, label)] chronological, already filtered to keydown events.
    """
    c = cfg(style)
    if not keys:
        return
    if c["mode"] == "line":
        return _draw_lines(dst, keys, now, c, frame_h, frame_w)
    if c["mode"] == "panel":
        return _draw_panel(dst, keys, now, c, frame_h, frame_w)
    hold = float(c["hold"])
    show_ime = bool(c.get("show_ime", False))
    active = []
    for k in keys:
        # A keycap stands for a *physical* key. An IME commit is not one, so it is
        # left out unless asked for -- otherwise typing Chinese paints Chinese
        # characters onto keycaps, which is not a key.
        if not show_ime and isinstance(k, dict) and k.get("kind") == "ime":
            continue
        ent = _entry(k, c["view"])
        age = now - ent[0]
        if 0 <= age <= hold:
            active.append((age, ent[1]))
    active = active[-int(c["max_visible"]):]
    if not active:
        return

    font_px = max(18, int(frame_h * float(c["size"])))
    sprites = []
    press_for = float(c.get("press_time", 0.14))
    for age, label in active:
        sp = cap(label, font_px, style, pressed=(age < press_for))
        fade = min(1.0, age / max(1e-3, c["fade_in"])) * \
            min(1.0, max(0.0, (hold - age) / max(1e-3, c["fade_out"])))
        pop = (0.90 + 0.10 * (age / 0.09)) if age < 0.09 else 1.0
        sprites.append((sp, fade, pop))

    gap = max(4, int(font_px * 0.14))
    widths = [int(sp.width * pop) for sp, _, pop in sprites]
    total = sum(widths) + gap * (len(sprites) - 1)
    max_h = max(sp.height for sp, _, _ in sprites)
    margin = int(frame_h * float(c["margin"]))

    op = max(0.0, min(1.0, float(c.get("opacity", 1.0))))
    x, y = _place(c, frame_w, frame_h, total, max_h)

    for (sp, fade, pop), wdt in zip(sprites, widths):
        hgt = int(sp.height * pop)
        s2 = sp.resize((max(2, wdt), max(2, hgt)), Image.LANCZOS)
        paste_rgba(dst, s2, x, y, fade * op)
        x += wdt + gap
