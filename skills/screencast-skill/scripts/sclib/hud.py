"""hud.py — the two moments a take needs a human-facing cue.

Everything else in this kit is a file. These two are the exceptions, because
they exist for the person sitting in front of the machine, not for the agent:

  countdown  3..1, on screen and audible, *before* ffmpeg starts
  note       a small tick after the take is safe on disk

Surface: Apple's Liquid Glass. `NSGlassEffectView` is the real thing (macOS 26+);
older systems fall back to `NSVisualEffectView` with the same drawing, which
still blurs the actual backdrop. Both cues share one visual language -- same
material, same continuous-curvature corners, same hairline rim, same type ramp,
same accent -- so the pair reads as one product instead of two dialogs.

Three hard rules, because a recording tool that steals focus or lands in its own
footage is worse than no cue at all:

  * never activate the app -- an accessory window ordered front regardless does
    not take key focus, so the target window stays frontmost and the `expect`
    guard in a drive script still passes;
  * click-through (`ignoresMouseEvents`) -- it must not eat a click aimed at the
    app underneath;
  * gone before the first frame. Order the window out, keep the run loop alive
    long enough for the window server to recomposite, then exit; `rec` waits
    again before starting the capture.

Usage (internal; `rec` drives it):

    python -m sclib.hud countdown 3
    python -m sclib.hud note "12.3s · 8.4 MB"

If AppKit or a window server is unavailable this degrades to silence -- a
headless run must never fail because it could not draw a circle.
"""

import math
import os
import sys
import threading
import time

import objc

try:
    from AppKit import (
        NSApplication, NSApp, NSWindow, NSView, NSColor, NSBezierPath,
        NSAttributedString, NSFont, NSTimer, NSScreen,
        NSMakeRect, NSMakeSize, NSBackingStoreBuffered,
        NSWindowStyleMaskBorderless, NSApplicationActivationPolicyAccessory,
        NSSound, NSVisualEffectView,
        NSVisualEffectMaterialHUDWindow, NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectStateActive, NSFontAttributeName,
        NSForegroundColorAttributeName, NSFontDescriptor,
        NSFontDescriptorSystemDesignRounded,
    )
    _HAVE_APPKIT = True
except Exception:                                   # pragma: no cover
    _HAVE_APPKIT = False

try:
    from AppKit import NSGlassEffectView            # macOS 26+
    _HAVE_GLASS = True
except Exception:                                   # pragma: no cover
    _HAVE_GLASS = False

# --- the shared visual language -------------------------------------------
PAD = 26.0          # room around the glass for the soft drop shadow
CAP_H = 60.0        # completion capsule height


def _sound(name, volume=0.28):
    try:
        s = NSSound.soundNamed_(name)
        if s:
            s.setVolume_(volume)
            s.play()
    except Exception:
        pass


# --- type ramp -------------------------------------------------------------
def _font(size, weight="regular", rounded=False):
    try:
        if rounded:
            # documented route: take the *weighted* system font's descriptor,
            # swap its design, then rebuild -- asking the class directly gives a
            # descriptor that resolves back to the default face
            base = NSFont.systemFontOfSize_weight_(size, 0.3 if weight == "semibold" else 0.0)
            desc = base.fontDescriptor().fontDescriptorWithDesign_(
                NSFontDescriptorSystemDesignRounded)
            if desc is not None:
                f = NSFont.fontWithDescriptor_size_(desc, size)
                if f is not None:
                    return f
    except Exception:
        pass
    if weight == "semibold":
        return NSFont.systemFontOfSize_weight_(size, 0.3)
    if weight == "bold":
        return NSFont.boldSystemFontOfSize_(size)
    return NSFont.systemFontOfSize_(size)


def _attr(text, size, color, weight="regular", rounded=False, tracking=0.0):
    d = {NSFontAttributeName: _font(size, weight, rounded),
         NSForegroundColorAttributeName: color}
    if tracking:
        try:
            from AppKit import NSKernAttributeName
            d[NSKernAttributeName] = tracking
        except Exception:
            pass
    return NSAttributedString.alloc().initWithString_attributes_(text, d)


def _width(text, size, weight="regular", rounded=False):
    return _attr(text, size, NSColor.labelColor(), weight, rounded).size().width


# --- views -----------------------------------------------------------------
class _ContentView(NSView):
    """Everything drawn on top of the glass: rim, sheen, ring, badge, type."""

    def initWithFrame_(self, frame):
        self = objc.super(_ContentView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.kind = "squircle"
        self.label = "3"
        self.sub = ""
        self.progress = 1.0
        self.tint = None
        self.icon = "none"
        return self

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        try:
            _draw(self)
        except Exception:
            pass


def _label(alpha=1.0, secondary=False):
    """Semantic colours, never a hard-coded one.

    The regular glass variant adapts to the system appearance and to the
    luminosity of what sits behind it, so the text has to come from the label
    palette or it will fight the material in one of the two appearances.
    """
    c = NSColor.secondaryLabelColor() if secondary else NSColor.labelColor()
    return c.colorWithAlphaComponent_(alpha)


def _draw(v):
    """Content only.

    The glass paints its own edge, specular and lens band; a second drawn
    outline inside it reads as a box inside a box, and a hand-drawn shadow
    reads as grey debris around the corners. So this view adds type and the
    progress ring -- nothing that competes with the material.
    """
    b = v.bounds()
    w, h = b.size.width, b.size.height
    if v.kind == "capsule":
        _draw_capsule(v, w, h)
    else:
        _draw_ring(v, w, h)


def _draw_ring(v, w, h):
    cx, cy = w / 2.0, h / 2.0
    r = min(w, h) / 2.0 - 20.0

    track = NSBezierPath.bezierPath()
    track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        (cx, cy), r, 90.0, -270.0, True)
    track.setLineWidth_(3.5)
    _label(0.14).set()
    track.stroke()

    frac = max(0.0, min(1.0, v.progress))
    if frac > 0.002:
        arc = NSBezierPath.bezierPath()
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), r, 90.0, 90.0 - 360.0 * frac, True)
        arc.setLineWidth_(3.5)
        arc.setLineCapStyle_(1)
        _label(0.88).set()
        arc.stroke()

    s = _attr(v.label, 70, _label(0.95), "semibold")
    sz = s.size()
    s.drawAtPoint_(((w - sz.width) / 2.0, (h - sz.height) / 2.0 - 1.0))


def _draw_capsule(v, w, h):
    d = 22.0
    bx = 18.0
    by = (h - d) / 2.0
    mark = NSBezierPath.bezierPath()
    mark.moveToPoint_((bx + 6.2, by + 11.6))
    mark.lineToPoint_((bx + 9.6, by + 7.6))
    mark.lineToPoint_((bx + 16.0, by + 15.2))
    mark.setLineWidth_(2.2)
    mark.setLineCapStyle_(1)
    mark.setLineJoinStyle_(1)
    _label(0.90).set()
    mark.stroke()

    tx = bx + d + 12.0
    main = _attr(v.label, 15.0, _label(0.95), "semibold")
    ms = main.size()
    if v.sub:
        sub = _attr(v.sub, 11.5, _label(0.55, secondary=True))
        ss = sub.size()
        top = (h + ms.height + ss.height) / 2.0
        main.drawAtPoint_((tx, top - ms.height - 1.0))
        sub.drawAtPoint_((tx, top - ms.height - ss.height - 2.0))
    else:
        main.drawAtPoint_((tx, (h - ms.height) / 2.0))


# --- window ----------------------------------------------------------------
def _screen_frame():
    s = NSScreen.mainScreen()
    return s.frame() if s else NSMakeRect(0, 0, 1440, 900)


def _build(w, h, kind, top_center):
    """window -> [shadow, glass, content], all on the shared padding grid."""
    W, H = w + PAD * 2, h + PAD * 2
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    win.setLevel_(1000)
    win.setIgnoresMouseEvents_(True)
    win.setReleasedWhenClosed_(False)

    root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))

    content = _ContentView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    content.kind = kind

    box = NSMakeRect(PAD, PAD, w, h)
    if _HAVE_GLASS:
        glass = NSGlassEffectView.alloc().initWithFrame_(box)
        try:
            glass.setCornerRadius_(w / 2.0 if kind == "circle" else h / 2.0)
        except Exception:
            pass
    else:
        glass = NSVisualEffectView.alloc().initWithFrame_(box)
        glass.setMaterial_(NSVisualEffectMaterialHUDWindow)
        glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(NSVisualEffectStateActive)
    root.addSubview_(glass)

    # The overlay is a SIBLING at the glass's own frame, not its contentView:
    # NSGlassEffectView insets its contentView, which silently offsets every
    # shape drawn inside it by the inset amount.
    content.setFrame_(box)
    root.addSubview_(content)
    win.setContentView_(root)

    f = _screen_frame()
    x = f.origin.x + (f.size.width - W) / 2.0
    y = (f.origin.y + f.size.height - H - 34) if top_center else \
        (f.origin.y + (f.size.height - H) / 2.0)
    win.setFrameOrigin_((x, y))
    win.orderFrontRegardless()        # front WITHOUT activating the app
    return win, content, glass


def _run_app(tick, done_after, close=None):
    """Spin the run loop until `tick` says stop or `done_after` elapses."""
    started = time.time()

    # Two phases on purpose: `orderOut` is a round trip to the window server, so
    # the process must stay alive while the loop runs for the removal to be
    # composited. Exiting straight away leaves the last frame on screen -- and
    # that frame becomes the first frame of the take.
    closing = []

    def fire(timer):
        if closing:
            if time.time() - closing[0] >= 0.30:
                timer.invalidate()
                os._exit(0)
            return
        if tick() or time.time() - started > done_after:
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
            closing.append(time.time())

    # Watchdog: a HUD that cannot leave the screen is worse than no HUD -- a
    # stuck one would sit at the centre of the display forever.
    watchdog = threading.Timer(done_after + 6.0, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()

    NSApplication.sharedApplication()
    NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / 60.0, True, fire)
    NSApp.run()


# --- public cues -----------------------------------------------------------
def countdown(seconds):
    if not _HAVE_APPKIT or seconds <= 0:
        return countdown_text(seconds)
    win, view, _glass = _build(176.0, 176.0, "circle", top_center=False)

    t0 = time.time()
    last = [None]

    def tick():
        left = seconds - (time.time() - t0)
        if left <= 0:
            view.progress = 0.0
            view.label = ""
            view.display()
            _sound("Glass", 0.30)
            return True
        n = int(left) + 1
        if n != last[0]:
            last[0] = n
            _sound("Tink", 0.20)
        view.label = str(n)
        view.progress = left - int(left)      # 1 -> 0 inside each second
        view.display()
        return False

    _run_app(tick, seconds + 1.0, close=lambda: win.orderOut_(None))
    return 0


def countdown_text(seconds):
    """No window server: still count in the terminal, never silently skip."""
    for n in range(int(seconds), 0, -1):
        sys.stderr.write("\r  starting in %d " % n)
        sys.stderr.flush()
        time.sleep(1.0)
    sys.stderr.write("\r  recording          \n")
    sys.stderr.flush()
    return 0


def note(text, sub="", seconds=2.4):
    if not _HAVE_APPKIT:
        return 0
    body = max(_width(text, 15.0, "semibold"),
               _width(sub, 11.5) if sub else 0.0)
    w = max(196.0, 22.0 + 18.0 + 22.0 + 14.0 + body + 24.0)
    win, view, glass = _build(w, CAP_H, "capsule", top_center=True)
    view.label = text
    view.sub = sub
    win.setAlphaValue_(0.0)
    _sound("Pop", 0.30)

    t0 = time.time()

    def tick():
        el = time.time() - t0
        alpha = min(1.0, el / 0.14)
        if el > seconds - 0.38:
            alpha = max(0.0, (seconds - el) / 0.38)
        win.setAlphaValue_(alpha)
        return el > seconds

    _run_app(tick, seconds + 0.5,
             close=lambda: (win.setAlphaValue_(0.0), win.orderOut_(None)))
    return 0


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "countdown":
        return countdown(float(argv[2]) if len(argv) > 2 else 3.0)
    if cmd == "note":
        return note(argv[2] if len(argv) > 2 else "",
                    argv[3] if len(argv) > 3 else "")
    sys.stderr.write("hud: unknown command %r\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
