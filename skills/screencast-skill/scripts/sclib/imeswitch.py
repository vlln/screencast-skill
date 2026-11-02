"""imeswitch.py — park the keyboard on a plain ASCII layout while injecting.

Symptom it fixes: a scripted take types ASCII, the Chinese input method swallows
it into a composition buffer, and the app gets nothing (or a fragment). The
candidate bar in the corner is the giveaway -- that is the IME showing you the
keystrokes it took.

Two things this module learned the hard way:

  * the TIS calls must come from a process with a window-server connection.
    Calling them from a plain CLI hangs forever in `TISSelectInputSource`;
    `NSApplication.sharedApplication()` first, and the same call returns in
    milliseconds.
  * TIS returns CoreFoundation objects, so they are handled through ctypes
    throughout. Passing a raw pointer back into a pyobjc-typed CF function does
    not bridge and fails with "unrecognized selector".

`rec` runs this as a **subprocess with a timeout**, so even if a future macOS
makes these calls hang again, a recording can never be blocked by it.

    python -m sclib.imeswitch current
    python -m sclib.imeswitch ascii       # prints the id to restore, or "none"
    python -m sclib.imeswitch select <id>
"""

import ctypes
import sys

TIS = "/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox"
CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8 = 0x08000100

ASCII_IDS = ("com.apple.keylayout.ABC", "com.apple.keylayout.US",
             "com.apple.keylayout.USExtended")

_hit = _core = None


def _libs():
    global _hit, _core
    if _hit:
        return True
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication()      # window-server connection: required
        _hit = ctypes.CDLL(TIS)
        _core = ctypes.CDLL(CF)
    except Exception:
        return False
    for name, res, args in (
        ("TISCreateInputSourceList", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_bool]),
        ("TISGetInputSourceProperty", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
        ("TISSelectInputSource", ctypes.c_int32, [ctypes.c_void_p]),
        ("TISCopyCurrentKeyboardInputSource", ctypes.c_void_p, []),
    ):
        f = getattr(_hit, name)
        f.restype, f.argtypes = res, args
    for name, res, args in (
        ("CFArrayGetCount", ctypes.c_long, [ctypes.c_void_p]),
        ("CFArrayGetValueAtIndex", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_long]),
        ("CFStringGetCString", ctypes.c_bool,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
        ("CFStringGetLength", ctypes.c_long, [ctypes.c_void_p]),
        ("CFStringCreateWithCString", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]),
        ("CFBooleanGetValue", ctypes.c_bool, [ctypes.c_void_p]),
        ("CFRelease", None, [ctypes.c_void_p]),
    ):
        f = getattr(_core, name)
        f.restype, f.argtypes = res, args
    return True


def _prop(src, key):
    k = _core.CFStringCreateWithCString(None, key.encode(), UTF8)
    try:
        return _hit.TISGetInputSourceProperty(src, k)
    finally:
        _core.CFRelease(k)


def _text(cfs):
    if not cfs:
        return None
    n = _core.CFStringGetLength(cfs)
    buf = ctypes.create_string_buffer(n * 4 + 8)
    if not _core.CFStringGetCString(cfs, buf, len(buf), UTF8):
        return None
    return buf.value.decode("utf-8", "replace")


def _bool(src, key):
    v = _prop(src, key)
    if not v:
        return False
    try:
        return bool(_core.CFBooleanGetValue(v))
    except Exception:
        return False


def sources():
    if not _libs():
        return []
    arr = _hit.TISCreateInputSourceList(None, True)
    if not arr:
        return []
    # The array owns its elements: releasing it turns every `src` into a
    # dangling pointer, and selecting one of those hangs. This process is
    # short-lived, so the array is simply kept alive.
    out = []
    for i in range(_core.CFArrayGetCount(arr)):
        src = _core.CFArrayGetValueAtIndex(arr, i)
        sid = _text(_prop(src, "TISPropertyInputSourceID"))
        if sid:
            out.append((src, sid, _bool(src, "TISPropertyInputSourceIsSelected")))
    return out


def current():
    """The layout that is receiving keystrokes.

    `TISPropertyInputSourceIsSelected` is not populated for every source on
    every macOS build, so ask for the current keyboard source directly and fall
    back to the selection flag.
    """
    if _libs():
        src = _hit.TISCopyCurrentKeyboardInputSource()
        if src:
            sid = _text(_prop(src, "TISPropertyInputSourceID"))
            if sid:
                return sid
    for _s, sid, sel in sources():
        if sel:
            return sid
    return None


def select(source_id):
    for src, sid, _sel in sources():
        if sid == source_id:
            return _hit.TISSelectInputSource(src) == 0
    return False


def to_ascii():
    """Switch to a plain ASCII layout; return the id to put back."""
    before = current()
    have = [(src, sid) for src, sid, _sel in sources()]
    for want in ASCII_IDS:
        for src, sid in have:
            if sid == want:
                if _hit.TISSelectInputSource(src) != 0:
                    return "none"
                return "none" if before in (None, want) else before
    return "none"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "current"
    if cmd == "current":
        print(current() or "none")
    elif cmd == "ascii":
        print(to_ascii())
    elif cmd == "select":
        if len(argv) < 3 or argv[2] in ("", "none"):
            return 0
        return 0 if select(argv[2]) else 1
    else:
        sys.stderr.write(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
