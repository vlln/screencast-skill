"""Pure-Python global input + window-focus recorder.

Replaces the old Objective-C `evtap` binary. Same JSONL contract, so the
renderer does not care which one produced the file.

Why pure Python: shipping a compiled helper forces every user to install Xcode
Command Line Tools (~2GB). `pyobjc` exposes CGEventTapCreate directly, and the
event rate we care about (mouse moves ~60-200Hz) is well inside what a Python
callback can handle.

Requires: Accessibility / Input Monitoring permission.
          + Screen Recording for window titles.

Usage: python3 -m sclib.events <out.jsonl> [--pin WINID] [--duration SEC]
"""

import json
import os
import signal
import sys
import time

import Quartz

# pyobjc does not export a few legacy CG symbols; fall back to their header values.
_SCROLL_AXIS1 = getattr(Quartz, "kCGScrollWheelEventDeltaAxis1", 11)
_SCROLL_AXIS2 = getattr(Quartz, "kCGScrollWheelEventDeltaAxis2", 12)


def ax_trusted():
    """AXIsProcessTrusted is not bridged by pyobjc — call it via ctypes."""
    try:
        import ctypes
        lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return False

_stop = False
_fp = None
_t0 = 0.0

# last emitted window state (only emit deltas)
_l_front = None
_l_fr = None
_l_pin = None
_l_pr = None


def _on_signal(signum, frame):
    global _stop
    _stop = True


def _btn_name(n):
    return {0: "left", 1: "right", 2: "center"}.get(int(n), "other")


def _utf8(event):
    try:
        res = Quartz.CGEventKeyboardGetUnicodeString(event, 8, None, None)
        if isinstance(res, tuple):
            return res[1][: res[0]]
    except Exception:
        pass
    return ""


def _window_state(pin):
    """(front_id, front_rect, pin_id, pin_rect) from a front-to-back window list."""
    arr = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    front = None
    fr = None
    p_id = 0
    pr = None
    for d in arr or []:
        if int(d.get("kCGWindowLayer", 0)) != 0:
            continue
        b = d.get("kCGWindowBounds") or {}
        r = (float(b.get("X", 0)), float(b.get("Y", 0)),
             float(b.get("Width", 0)), float(b.get("Height", 0)))
        if front is None:
            front = int(d.get("kCGWindowNumber", 0))
            fr = r
        if pin and int(d.get("kCGWindowNumber", 0)) == pin:
            p_id = pin
            pr = r
    return front, fr, p_id, pr


def _poll_windows(pin):
    global _l_front, _l_fr, _l_pin, _l_pr
    front, fr, p_id, pr = _window_state(pin)
    changed = front != _l_front
    if fr and _l_fr:
        changed |= any(abs(fr[i] - _l_fr[i]) > 0.5 for i in range(4))
    if pin:
        changed |= p_id != _l_pin
        if pr and _l_pr:
            changed |= any(abs(pr[i] - _l_pr[i]) > 0.5 for i in range(4))
    if not changed:
        return
    _l_front, _l_fr, _l_pin, _l_pr = front, fr, p_id, pr
    fr = fr or (0, 0, 0, 0)
    pr = pr or (0, 0, 0, 0)
    _fp.write(
        '{"t":%.4f,"type":"win","front":%d,"fx":%.1f,"fy":%.1f,"fw":%.1f,"fh":%.1f,'
        '"pin":%d,"px":%.1f,"py":%.1f,"pw":%.1f,"ph":%.1f}\n'
        % (time.monotonic() - _t0, front or 0, fr[0], fr[1], fr[2], fr[3],
           p_id, pr[0], pr[1], pr[2], pr[3])
    )
    _fp.flush()


# --- what shape is the system cursor right now? ---------------------------
# macOS does not expose the current cursor id, but it does answer "which UI
# element is under this point". That is enough: the element's role decides
# whether the pointer should read as an arrow, an I-beam or a hand -- which is
# the behaviour Recordly has and a flat arrow does not.
SHAPE_BY_ROLE = {
    "AXTextField": "text", "AXTextArea": "text", "AXSearchField": "text",
    "AXComboBox": "text", "AXWebArea": "text",
    "AXButton": "pointer", "AXLink": "pointer", "AXMenuItem": "pointer",
    "AXCheckBox": "pointer", "AXRadioButton": "pointer",
    "AXPopUpButton": "pointer", "AXMenuButton": "pointer",
    "AXDockItem": "pointer", "AXTab": "pointer",
}

_AX = {}
_CURSOR = {"shape": None, "pt": None, "t": 0.0}


def _ax_init():
    if _AX:
        return _AX
    try:
        import ctypes
        import ctypes.util
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework"
                         "/Frameworks/HIServices.framework/HIServices")
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        ax.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        ax.AXUIElementCopyElementAtPosition.restype = ctypes.c_int
        ax.AXUIElementCopyElementAtPosition.argtypes = [
            ctypes.c_void_p, ctypes.c_float, ctypes.c_float,
            ctypes.POINTER(ctypes.c_void_p)]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        _AX.update(ax=ax, cf=cf, wide=ax.AXUIElementCreateSystemWide())
    except Exception:
        _AX["ax"] = None
    return _AX


def _role_at(x, y):
    a = _ax_init()
    if not a.get("ax"):
        return None
    import ctypes
    el = ctypes.c_void_p()
    if a["ax"].AXUIElementCopyElementAtPosition(
            a["wide"], x, y, ctypes.byref(el)) != 0 or not el:
        return None
    key = a["cf"].CFStringCreateWithCString(None, b"AXRole", 0x08000100)
    val = ctypes.c_void_p()
    ok = a["ax"].AXUIElementCopyAttributeValue(el, key, ctypes.byref(val))
    a["cf"].CFRelease(key)
    role = None
    if ok == 0 and val:
        n = a["cf"].CFStringGetLength(val)
        buf = ctypes.create_string_buffer(n * 4 + 8)
        if a["cf"].CFStringGetCString(val, buf, len(buf), 0x08000100):
            role = buf.value.decode("utf-8", "replace")
        a["cf"].CFRelease(val)
    a["cf"].CFRelease(el)
    return role


_SIGS = None


def _sig_table():
    """shape signature -> style, from the cursor atlas exported on this machine."""
    global _SIGS
    if _SIGS is not None:
        return _SIGS
    _SIGS = []
    try:
        import json
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "assets", "cursors", "meta.json")) as f:
            for style, info in json.load(f).get("cursors", {}).items():
                fp = info.get("fp")
                if fp:
                    _SIGS.append((style, bytes.fromhex(fp)))
    except Exception:
        pass
    return _SIGS


def _live_cursor_sig():
    """Fingerprint of the cursor the OS is showing right now.

    This is how Recordly does it: ask the system for the current cursor and
    match its shape, instead of guessing from what is underneath. It catches
    every shape macOS uses -- including the ones AX roles cannot describe --
    and it needs no Accessibility permission.
    """
    try:
        from AppKit import NSCursor, NSBitmapImageRep
        from . import cursors as cur
        c = NSCursor.currentSystemCursor()
        img = c.image() if c else None
        if img is None:
            return None
        rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
        if rep is None:
            return None
        return cur.fingerprint(bytes(rep.TIFFRepresentation()))
    except Exception:
        return None


def _poll_cursor_shape():
    now = time.monotonic()
    if now - _CURSOR["t"] < 0.12:
        return
    _CURSOR["t"] = now
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    last = _CURSOR["pt"]
    if last and abs(p.x - last[0]) < 3 and abs(p.y - last[1]) < 3:
        return
    _CURSOR["pt"] = (p.x, p.y)

    # 1. exact: match the live cursor image against the atlas
    fp = _live_cursor_sig()
    from . import cursors as _cur
    shape = _cur.nearest(fp, _sig_table()) if fp else None
    role = "sprite"

    # 2. fallback: ask what element is under the pointer
    if shape is None:
        role = _role_at(p.x, p.y)
        if role is None:
            return
        shape = SHAPE_BY_ROLE.get(role, "arrow")
    if shape != _CURSOR["shape"]:
        _CURSOR["shape"] = shape
        _fp.write('{"t":%.4f,"type":"cursor","shape":"%s","role":"%s"}\n'
                  % (now - _t0, shape, role))
        _fp.flush()


def _make_callback():
    def cb(proxy, etype, event, refcon):
        global _fp
        try:
            t = time.monotonic() - _t0
            if etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown,
                         Quartz.kCGEventOtherMouseDown):
                p = Quartz.CGEventGetLocation(event)
                n = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber)
                c = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventClickState)
                _fp.write('{"t":%.4f,"type":"down","x":%.2f,"y":%.2f,"btn":"%s","count":%d}\n'
                          % (t, p.x, p.y, _btn_name(n), c))
            elif etype in (Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseUp,
                           Quartz.kCGEventOtherMouseUp):
                p = Quartz.CGEventGetLocation(event)
                n = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber)
                _fp.write('{"t":%.4f,"type":"up","x":%.2f,"y":%.2f,"btn":"%s"}\n'
                          % (t, p.x, p.y, _btn_name(n)))
            elif etype in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDragged,
                           Quartz.kCGEventRightMouseDragged, Quartz.kCGEventOtherMouseDragged):
                p = Quartz.CGEventGetLocation(event)
                _fp.write('{"t":%.4f,"type":"move","x":%.2f,"y":%.2f}\n'
                          % (t, p.x, p.y))
            elif etype == Quartz.kCGEventScrollWheel:
                p = Quartz.CGEventGetLocation(event)
                dy = Quartz.CGEventGetIntegerValueField(
                    event, _SCROLL_AXIS1)
                dx = Quartz.CGEventGetIntegerValueField(
                    event, _SCROLL_AXIS2)
                _fp.write('{"t":%.4f,"type":"scroll","x":%.2f,"y":%.2f,"dx":%d,"dy":%d}\n'
                          % (t, p.x, p.y, dx, dy))
            elif etype == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                rep = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventAutorepeat)
                flags = Quartz.CGEventGetFlags(event)
                _fp.write('{"t":%.4f,"type":"keydown","code":%d,"text":%s,"flags":%d,"repeat":%d}\n'
                          % (t, code, json.dumps(_utf8(event)), flags, rep))
            elif etype == Quartz.kCGEventKeyUp:
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event)
                _fp.write('{"t":%.4f,"type":"keyup","code":%d,"text":%s,"flags":%d}\n'
                          % (t, code, json.dumps(_utf8(event)), flags))
            elif etype == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                _fp.write('{"t":%.4f,"type":"mod","flags":%d,"code":%d}\n'
                          % (t, flags, code))
        except Exception:
            # never let a bad event kill the tap
            pass
        return event
    return cb


def main(argv):
    global _fp, _t0, _l_front, _l_pin
    if len(argv) < 2:
        sys.stderr.write("usage: python3 -m sclib.events <out.jsonl> "
                         "[--pin WINID] [--duration SEC]\n")
        return 2
    outpath = argv[1]
    pin = 0
    duration = 0.0
    i = 2
    while i < len(argv):
        if argv[i] == "--pin" and i + 1 < len(argv):
            pin = int(argv[i + 1]); i += 2
        elif argv[i] == "--duration" and i + 1 < len(argv):
            duration = float(argv[i + 1]); i += 2
        else:
            i += 1

    _fp = open(outpath, "w")
    trusted = ax_trusted()
    _t0 = time.monotonic()
    _fp.write('{"t":0.0,"type":"start","trusted":%d,"pin":%d,"pid":%d,"wall":%.4f}\n'
              % (trusted, pin, os.getpid(), time.time()))
    _fp.flush()

    mask = 0
    for ev in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDragged,
               Quartz.kCGEventRightMouseDragged, Quartz.kCGEventOtherMouseDragged,
               Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown,
               Quartz.kCGEventOtherMouseDown, Quartz.kCGEventLeftMouseUp,
               Quartz.kCGEventRightMouseUp, Quartz.kCGEventOtherMouseUp,
               Quartz.kCGEventScrollWheel, Quartz.kCGEventKeyDown,
               Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged):
        mask |= Quartz.CGEventMaskBit(ev)

    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap,
                                  Quartz.kCGHeadInsertEventTap,
                                  Quartz.kCGEventTapOptionListenOnly,
                                  mask, _make_callback(), None)
    if not tap:
        _fp.write('{"t":0.0,"type":"error","msg":"event tap unavailable"}\n')
        _fp.close()
        sys.stderr.write("sclib.events: event tap unavailable "
                         "(needs Accessibility/Input Monitoring permission)\n")
        return 1

    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                              Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    _poll_windows(pin)
    _fp.flush()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    sys.stderr.write("TAP READY\n")
    sys.stderr.flush()

    start = time.monotonic()
    while not _stop:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.1, False)
        _poll_windows(pin)
        _poll_cursor_shape()
        if not Quartz.CGEventTapIsEnabled(tap):
            Quartz.CGEventTapEnable(tap, True)
        if duration > 0 and (time.monotonic() - start) >= duration:
            break

    _fp.write('{"t":%.4f,"type":"stop"}\n' % (time.monotonic() - _t0))
    _fp.flush()
    _fp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
