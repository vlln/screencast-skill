"""Pure-Python scripted input (replaces the old Objective-C `drive` binary).

Same script language as before, so existing demo scripts keep working:

    expect TextEdit      # abort unless this app is frontmost
    save / restore
    move X Y MS
    drag X0 Y0 X1 Y1 MS      # press at the first point, glide, release
    click / doubleclick
    type 任意文字
    key cmd+shift+a
    sleep MS

`expect` is a real safety valve, not decoration: these events go to whatever is
frontmost, so on a machine somebody is actually using, a stray click can land in
the wrong window.

Usage: python3 -m sclib.drive < script.txt
"""

import sys
import time

import Quartz
from AppKit import NSWorkspace

_cursor = None
_saved = None

# Every modifier bit we can leak, including the Fn bit that macOS sets on
# synthesized Home/arrow keys.
_MOD_BITS = (Quartz.kCGEventFlagMaskShift | Quartz.kCGEventFlagMaskControl |
             Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskCommand |
             0x00800000)


def _mods_down():
    st = Quartz.CGEventSourceFlagsState(
        Quartz.kCGEventSourceStateCombinedSessionState)
    return bool(st & _MOD_BITS)


def wait_mods_idle(timeout=3.0):
    """Block until no modifier is physically held.

    CGEventCreateKeyboardEvent seeds its flags from the LIVE hardware state, so
    a modifier a human happens to be holding is inherited by every synthesized
    keystroke, and two things go wrong at once:

      * the app treats the event as a shortcut instead of text -- TextEdit
        silently drops unicode-string events while Fn is down, which is how
        whole lines of a demo go missing;
      * with our keycode-0 text events a held Command turns every character
        into a menu shortcut (`a` -> Select All, `s` -> Save, `q` -> Quit).

    So: wait for a clear keyboard, and clear the flags explicitly anyway.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not _mods_down():
            return True
        time.sleep(0.03)
    return False


def _post(ev):
    if ev is not None:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _mouse(kind, pt, count=0):
    e = Quartz.CGEventCreateMouseEvent(None, kind, pt, 0)
    if count:
        Quartz.CGEventSetIntegerValueField(e, Quartz.kCGMouseEventClickState, count)
    # mouse events inherit live modifiers the same way keystrokes do, and a
    # leaked Command would turn a click into cmd-click
    Quartz.CGEventSetFlags(e, 0)
    return e


def _pos():
    return Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))


def _ease(t):
    return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2


def move_to(target, ms):
    global _cursor
    if ms <= 0:
        _post(_mouse(Quartz.kCGEventMouseMoved, target))
        _cursor = target
        return
    steps = max(2, int(ms / 8))
    a = _cursor
    for i in range(1, steps + 1):
        p = _ease(i / steps)
        q = Quartz.CGPointMake(a.x + (target.x - a.x) * p,
                               a.y + (target.y - a.y) * p)
        _post(_mouse(Quartz.kCGEventMouseMoved, q))
        time.sleep(0.008)
    _cursor = target


def drag(p0, p1, ms):
    """Press, glide, release -- resizing a window is not otherwise expressible.

    The motion has to be `kCGEventLeftMouseDragged`; plain MouseMoved while the
    button is down does not reach the window server as a drag, so nothing
    resizes and the gesture looks like a stray click.
    """
    global _cursor
    move_to(p0, 260)
    _post(_mouse(Quartz.kCGEventLeftMouseDown, _cursor, 1))
    time.sleep(0.08)
    steps = max(2, int(ms / 8))
    a = p0
    for i in range(1, steps + 1):
        p = _ease(i / steps)
        q = Quartz.CGPointMake(a.x + (p1.x - a.x) * p,
                               a.y + (p1.y - a.y) * p)
        _post(_mouse(Quartz.kCGEventLeftMouseDragged, q, 1))
        time.sleep(0.008)
    _cursor = p1
    time.sleep(0.08)
    _post(_mouse(Quartz.kCGEventLeftMouseUp, _cursor, 1))
    time.sleep(0.05)


def click_at(count=1):
    for c in range(1, count + 1):
        _post(_mouse(Quartz.kCGEventLeftMouseDown, _cursor, c))
        time.sleep(0.028)
        _post(_mouse(Quartz.kCGEventLeftMouseUp, _cursor, c))
        time.sleep(0.040)


def type_text(s):
    if not wait_mods_idle():
        sys.stderr.write("drive: warning: modifiers still held, typing anyway\n")
    for ch in s:
        code = _TYPE_CODES.get(ch.lower(), 0)
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            Quartz.CGEventKeyboardSetUnicodeString(e, 1, ch)
            # never inherit the live modifier state -- see wait_mods_idle
            Quartz.CGEventSetFlags(e, 0)
            _post(e)
            time.sleep(0.016 if down else 0.011)


_KEYMAP = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
}


# named keys — `key return` / `key tab` were simply not expressible before
_SPECIAL = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "esc": 53, "escape": 53,
    "delete": 51, "backspace": 51, "forwarddelete": 117,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
}


# Space needs its own keycode: falling back to 0 would make the recorder read
# the char back as a literal "A" on the key overlay.
_TYPE_CODES = dict(_KEYMAP)
_TYPE_CODES[" "] = 49


def key_combo(flags, ch):
    if len(ch) > 1 and ch.lower() in _SPECIAL:
        code = _SPECIAL[ch.lower()]
    else:
        code = _KEYMAP.get(ch.lower())
    if code is None:
        sys.stderr.write("drive: no keycode for %r\n" % ch)
        return
    d = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    Quartz.CGEventSetFlags(d, flags)
    _post(d)
    time.sleep(0.024)
    u = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    Quartz.CGEventSetFlags(u, flags)
    _post(u)


def frontmost_owner():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()) if app else None


def main(argv):
    global _cursor, _saved
    _cursor = _pos()
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if line.startswith("expect "):
            want = line[7:].strip()
            got = frontmost_owner()
            if not got or got.lower() != want.lower():
                sys.stderr.write("drive: ABORT expected frontmost %r, got %r\n"
                                 % (want, got))
                return 3
        elif line.strip() == "save":
            _saved = _cursor
        elif line.strip() == "restore":
            if _saved is not None:
                move_to(_saved, 220)
        elif line.startswith("move "):
            parts = line[5:].split()
            x, y = float(parts[0]), float(parts[1])
            ms = int(parts[2]) if len(parts) > 2 else 400
            move_to(Quartz.CGPointMake(x, y), ms)
        elif line.startswith("drag "):
            parts = line[5:].split()
            drag(Quartz.CGPointMake(float(parts[0]), float(parts[1])),
                 Quartz.CGPointMake(float(parts[2]), float(parts[3])),
                 int(parts[4]) if len(parts) > 4 else 500)
        elif line.strip() == "click":
            click_at(1)
        elif line.strip() == "doubleclick":
            click_at(2)
        elif line.startswith("type "):
            type_text(line[5:])
        elif line.startswith("key "):
            spec = line[4:].strip()
            flags = 0
            while True:
                if spec.startswith("cmd+"):
                    flags |= Quartz.kCGEventFlagMaskCommand; spec = spec[4:]
                elif spec.startswith("shift+"):
                    flags |= Quartz.kCGEventFlagMaskShift; spec = spec[6:]
                elif spec.startswith("alt+"):
                    flags |= Quartz.kCGEventFlagMaskAlternate; spec = spec[4:]
                elif spec.startswith("ctrl+"):
                    flags |= Quartz.kCGEventFlagMaskControl; spec = spec[5:]
                else:
                    break
            if spec:
                key_combo(flags, spec if len(spec) > 1 and spec.lower() in _SPECIAL else spec[0])
        elif line.startswith("sleep "):
            time.sleep(int(line[6:]) / 1000.0)
        else:
            sys.stderr.write("drive: unknown command %r\n" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
