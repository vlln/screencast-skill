"""Export the real macOS cursor artwork to PNG, for use as cursor-layer assets.

Recordly does the same thing in Swift (SystemCursorAssets.swift); AppKit's
NSCursor already holds the artwork, so pyobjc is enough here.

    python3 -m sclib.cursors            # -> assets/cursors/*.png + meta.json
    python3 -m sclib.cursors --out DIR

Then point a theme at the result:

    "cursor": { "assets": "assets/cursors/meta.json" }

Hotspots are exported as fractions of the image size, so they stay correct at
any render scale.
"""

import json
import os
import re
import sys

CURSORS = {
    "arrow": "arrowCursor",
    "text": "IBeamCursor",
    "pointer": "pointingHandCursor",
    "crosshair": "crosshairCursor",
    "open-hand": "openHandCursor",
    "closed-hand": "closedHandCursor",
    "resize-ew": "resizeLeftRightCursor",
    "resize-ns": "resizeUpDownCursor",
    "not-allowed": "operationNotAllowedCursor",
}


# Apple ships the actual cursor artwork as vector PDFs plus a plist with the
# hotspot. This is the same art the OS draws, so the overlay stops being
# "somebody's hand-drawn arrow" and becomes the cursor the viewer already knows.
# The arrow is the one exception -- it is drawn in code, not shipped as a
# resource -- so it keeps the vector fallback.
RESOURCE_DIR = ("/System/Library/Frameworks/ApplicationServices.framework/"
                "Versions/A/Frameworks/HIServices.framework/Versions/A/"
                "Resources/cursors")
SYSTEM_MAP = {
    # Apple names these by the text they serve, not by the glyph: the normal
    # upright I-beam lives in "ibeamhorizontal". Getting this backwards draws a
    # sideways I-beam, which reads as "something is broken".
    "text": "ibeamhorizontal",
    "pointer": "pointinghand",
    "crosshair": "cross",
    "open-hand": "openhand",
    "closed-hand": "closedhand",
    "move": "move",
    "cell": "cell",
    "not-allowed": "notallowed",
    "zoom-in": "zoomin",
    "zoom-out": "zoomout",
    "resize-ew": "resizeleftright",
    "resize-ns": "resizeupdown",
    "help": "help",
}


def fingerprint(data):
    """16x16 alpha of a cursor sprite, as bytes -- what the matcher compares.

    An exact hash cannot work here: the sprite the OS hands back at runtime was
    rasterised by the system, not by us, so antialiasing differs. Nearest
    neighbour on the alpha grid survives that, a SHA does not.
    """
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return bytes(img.split()[3].resize((16, 16), Image.BILINEAR).tobytes())


def nearest(fp, table, limit=14.0):
    """Nearest style for a fingerprint, or None when nothing is close."""
    best, best_d = None, 1e9
    for style, ref in table:
        d = sum(abs(a - b) for a, b in zip(fp, ref)) / float(len(fp))
        if d < best_d:
            best, best_d = style, d
    return best if best_d <= limit else None


def signature(data):
    """A shape fingerprint of a cursor image, scale-independent.

    Downscale to 16x16 and keep the alpha: two sprites of the same cursor at
    different backing sizes must produce the same signature, because that is
    what lets the recorder recognise the live cursor at runtime.
    """
    import hashlib
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    a = img.split()[3].resize((16, 16), Image.BILINEAR)
    return hashlib.sha1(bytes(a.tobytes())).hexdigest()[:16]


def export_system(outdir, px=96):
    """Rasterise Apple's own cursor PDFs to PNG + hotspot, locally.

    Nothing is redistributed: the PNGs are generated from the machine's own
    system files on install.
    """
    import plistlib
    from AppKit import (NSImage, NSBitmapImageRep, NSPNGFileType,
                        NSMakeSize, NSMakeRect)

    if not os.path.isdir(RESOURCE_DIR):
        return None, {}
    os.makedirs(outdir, exist_ok=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    meta = {}
    for style, folder in SYSTEM_MAP.items():
        d = os.path.join(RESOURCE_DIR, folder)
        pdf = os.path.join(d, "cursor.pdf")
        if not os.path.exists(pdf):
            continue
        try:
            with open(os.path.join(d, "info.plist"), "rb") as f:
                info = plistlib.load(f)
        except Exception:
            info = {}

        img = NSImage.alloc().initWithContentsOfFile_(pdf)
        if img is None:
            continue
        w, h = img.size().width, img.size().height
        if w <= 0 or h <= 0:
            continue
        # preserve aspect, target `px` tall
        tw, th = max(1, int(round(px * w / h))), int(px)
        img.setSize_(NSMakeSize(tw, th))
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, tw, th, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
        from AppKit import NSGraphicsContext
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        img.drawInRect_(NSMakeRect(0, 0, tw, th))
        NSGraphicsContext.restoreGraphicsState()
        data = rep.representationUsingType_properties_(NSPNGFileType, {})
        name = "%s.png" % style
        path = os.path.join(outdir, name)
        if not data.writeToFile_atomically_(path, True):
            continue

        # hotspot from the plist is in the PDF's own point space
        hx = float(info.get("hotx", w / 2.0))
        hy = float(info.get("hoty", h / 2.0))
        meta[style] = {
            "path": os.path.relpath(path, root),
            "hotspot": [round(min(1.0, max(0.0, hx / w)), 4),
                        round(min(1.0, max(0.0, hy / h)), 4)],
            "sig": signature(bytes(data)),
            "fp": fingerprint(bytes(data)).hex(),
        }

    menupath = os.path.join(outdir, "meta.json")
    with open(menupath, "w") as f:
        json.dump({"version": 2, "source": "macOS system cursor resources",
                   "cursors": meta}, f, indent=2)
    return menupath, meta


def export(outdir, scale=3):
    from AppKit import NSBitmapImageRep, NSPNGFileType, NSCursor

    os.makedirs(outdir, exist_ok=True)
    meta = {}
    for style, sel in CURSORS.items():
        try:
            cur = getattr(NSCursor, sel)()
            img = cur.image()
            if img is None:
                continue
            size = img.size()
            w, h = float(size.width), float(size.height)
            if w <= 0 or h <= 0:
                continue

            tiff = img.TIFFRepresentation()
            rep = NSBitmapImageRep.imageRepWithData_(tiff)
            if rep is None:
                continue
            # upscale for crisper scaling in the renderer
            rep.setSize_((w * scale, h * scale))
            data = rep.representationUsingType_properties_(NSPNGFileType, {})
            path = os.path.join(outdir, "%s.png" % style)
            # store a skill-relative path so the directory stays movable
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rel = os.path.relpath(path, root)
            if not data.writeToFile_atomically_(path, True):
                continue

            hs = cur.hotSpot()
            meta[style] = {
                "path": rel,
                "hotspot": [round(float(hs.x) / w, 4), round(float(hs.y) / h, 4)],
            }
        except Exception as e:                      # pragma: no cover
            sys.stderr.write("cursors: %s failed: %s\n" % (style, e))

    menupath = os.path.join(outdir, "meta.json")
    with open(menupath, "w") as f:
        json.dump({"version": 1, "cursors": meta}, f, indent=2)
    return menupath, meta


# --------------------------------------------------------------------------
# importing a PNG set (e.g. a downloaded X11 / theme cursor pack)
# --------------------------------------------------------------------------
ALIASES = {
    "default": "arrow", "arrow": "arrow", "left_ptr": "arrow", "normal": "arrow",
    "top_left_arrow": "arrow", "pointer-arrow": "arrow",
    "text": "text", "ibeam": "text", "i-beam": "text", "xterm": "text",
    "text_cursor": "text",
    "pointer": "pointer", "hand2": "pointer", "hand": "pointer",
    "pointing_hand": "pointer", "pointinghand": "pointer",
    "crosshair": "crosshair", "cross": "crosshair", "tcross": "crosshair",
    "open-hand": "open-hand", "open_hand": "open-hand", "hand1": "open-hand",
    "closed-hand": "closed-hand", "closed_hand": "closed-hand",
    "fleur": "closed-hand", "grabbing": "closed-hand",
    "resize-ew": "resize-ew", "sb_h_double_arrow": "resize-ew", "ew-resize": "resize-ew",
    "col-resize": "resize-ew",
    "resize-ns": "resize-ns", "sb_v_double_arrow": "resize-ns", "ns-resize": "resize-ns",
    "row-resize": "resize-ns",
    "not-allowed": "not-allowed", "crossed_circle": "not-allowed",
    "no-drop": "not-allowed", "forbidden": "not-allowed",
}

# arrow's hotspot is its tip; the rest are conventionally centred
DEFAULT_HOTSPOTS = {
    "arrow": [0.0, 0.0],
    "pointer": [0.30, 0.05],
    "text": [0.5, 0.5],
    "crosshair": [0.5, 0.5],
    "open-hand": [0.5, 0.4],
    "closed-hand": [0.5, 0.4],
    "resize-ew": [0.5, 0.5],
    "resize-ns": [0.5, 0.5],
    "not-allowed": [0.5, 0.5],
}


def import_pngs(srcdir, outdir=None, sizes=None):
    """Build a cursor set from a directory of PNGs.

    Filenames are matched loosely against ALIASES, so a pack that ships
    `left_ptr.png` / `hand2.png` / `xterm.png` works without renaming. When a
    style has several sizes, the largest is taken.
    """
    from PIL import Image

    outdir = outdir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "cursors-imported")
    os.makedirs(outdir, exist_ok=True)

    best = {}
    for dirpath, _dirs, files in os.walk(srcdir):
        for fn in files:
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in (".png", ".webp"):
                continue
            low = stem.lower()
            # strip trailing size markers like -32 / _48 / @2x
            base = re.sub(r"[-_@]?\d+$", "", low)
            style = ALIASES.get(low) or ALIASES.get(base)
            if not style:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with Image.open(path) as im:
                    w = im.width
            except Exception:
                continue
            if style not in best or w > best[style][0]:
                best[style] = (w, path)

    meta = {}
    for style, (_w, path) in best.items():
        dst = os.path.join(outdir, "%s.png" % style)
        Image.open(path).convert("RGBA").save(dst)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rel = os.path.relpath(dst, root)
        # only keep a relative path when it actually lives inside the skill;
        # `../..` chains break the moment the directory is moved
        meta[style] = {"path": rel if not rel.startswith("..") else dst,
                       "hotspot": DEFAULT_HOTSPOTS.get(style, [0.5, 0.5])}

    menupath = os.path.join(outdir, "meta.json")
    with open(menupath, "w") as f:
        json.dump({"version": 1, "cursors": meta}, f, indent=2)
    return menupath, meta


def main(argv):
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "assets", "cursors")
    imp = None
    i = 1
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            outdir = argv[i + 1]
            i += 2
        elif argv[i] == "--import" and i + 1 < len(argv):
            imp = argv[i + 1]
            i += 2
        else:
            i += 1
    if imp:
        path, meta = import_pngs(imp, outdir)
        print(path)
        for k, v in meta.items():
            print("  %-12s %s  hotspot=%s" % (k, os.path.basename(v["path"]), v["hotspot"]))
        if not meta:
            print("no recognizable cursor PNGs found — see docs/CURSORS.md for names")
        return 0
    path, meta = export(outdir)
    print(path)
    for k, v in meta.items():
        print("  %-12s %s  hotspot=%s" % (k, os.path.basename(v["path"]), v["hotspot"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
