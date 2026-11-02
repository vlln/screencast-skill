"""Shared helpers for effect layers.

Every layer is a pure function of (time, plan, theme) -> pixels. Layers never
know about each other, so a theme can restyle or a contributor can replace one
layer without touching the others.

Antialiasing rule: Pillow has no AA primitives, so shapes are drawn at SS×
resolution and downscaled with LANCZOS. Drawing at native size is what produced
stair-stepped corners in the first version.
"""

SS = 3


def paste_rgba(dst, sprite, x, y, opacity=1.0):
    """Alpha-blend a RGBA sprite onto an RGB image at (x, y) top-left."""
    if sprite is None or opacity <= 0.004:
        return
    if opacity < 0.995:
        a = sprite.getchannel("A").point(lambda v: int(v * opacity))
        sprite = sprite.copy()
        sprite.putalpha(a)
    dst.paste(sprite, (int(round(x)), int(round(y))), sprite)


def ease_out_cubic(x):
    x = min(max(x, 0.0), 1.0)
    return 1.0 - (1.0 - x) ** 3


def clamp01(x):
    return min(max(x, 0.0), 1.0)
