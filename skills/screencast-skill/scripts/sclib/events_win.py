"""Windows input + window recorder (pynput), same JSONL contract as sclib.events.

⚠️  UNVERIFIED ON WINDOWS — see sclib/backends/windows.py.

Contract notes
  * timestamps are seconds since start, same as macOS
  * the `start` event carries `wall` (epoch) so the video clock can be aligned
  * modifier bits deliberately reuse the macOS mask values (⌃1<<18 ⌥1<<19
    ⇧1<<17 ⌘1<<20) so sclib/plan.py's key labelling works unchanged
  * special keys put a human name in `text` ("return", "tab", ...), because
    Windows virtual-key codes are not the macOS keycodes the labeller knows

Usage: python3 -m sclib.events_win <out.jsonl> [--pin HWND] [--duration SEC]
"""

import json
import os
import signal
import sys
import threading
import time

_stop = False
_t0 = 0.0
_fp = None
_lock = threading.Lock()
_flags = 0

MOD_BITS = {
    "ctrl": 1 << 18, "ctrl_l": 1 << 18, "ctrl_r": 1 << 18,
    "alt": 1 << 19, "alt_l": 1 << 19, "alt_r": 1 << 19, "alt_gr": 1 << 19,
    "shift": 1 << 17, "shift_l": 1 << 17, "shift_r": 1 << 17,
    "cmd": 1 << 20, "cmd_l": 1 << 20, "cmd_r": 1 << 20,
}

SPECIAL_NAMES = {
    "space": "space", "enter": "return", "tab": "tab", "esc": "esc",
    "backspace": "delete", "delete": "fwd del", "home": "home", "end": "end",
    "page_up": "page up", "page_down": "page down",
    "up": "up", "down": "down", "left": "left", "right": "right",
}

_last_win = None


def _emit(obj):
    with _lock:
        _fp.write(json.dumps(obj) + "\n")


def _t():
    return time.monotonic() - _t0


def _poll_windows(pin):
    """Record foreground window changes as `win` events."""
    global _last_win
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        fg = int(user32.GetForegroundWindow())
        r = wt.RECT()
        user32.GetWindowRect(fg, ctypes.byref(r))
        cur = (fg, r.left, r.top, r.right - r.left, r.bottom - r.top)
        pr = (0, 0, 0, 0)
        if pin:
            rr = wt.RECT()
            user32.GetWindowRect(int(pin), ctypes.byref(rr))
            pr = (rr.left, rr.top, rr.right - rr.left, rr.bottom - rr.top)
        if cur == _last_win:
            return
        _last_win = cur
        _emit({"t": round(_t(), 4), "type": "win", "front": cur[0],
               "fx": cur[1], "fy": cur[2], "fw": cur[3], "fh": cur[4],
               "pin": int(pin) if pin else 0,
               "px": pr[0], "py": pr[1], "pw": pr[2], "ph": pr[3]})
    except Exception:
        pass


def main(argv):
    global _fp, _t0
    if len(argv) < 2:
        sys.stderr.write("usage: python3 -m sclib.events_win <out.jsonl> "
                         "[--pin HWND] [--duration SEC]\n")
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

    try:
        from pynput import keyboard, mouse
    except ImportError:
        sys.stderr.write("sclib.events_win: pynput is required "
                         "(pip install pynput)\n")
        return 1

    _fp = open(outpath, "w")
    _t0 = time.monotonic()
    _fp.write(json.dumps({"t": 0.0, "type": "start", "trusted": True,
                          "pin": pin, "pid": os.getpid(),
                          "wall": time.time()}) + "\n")
    _fp.flush()

    def on_move(x, y):
        _emit({"t": round(_t(), 4), "type": "move", "x": x, "y": y})

    def on_click(x, y, button, pressed):
        btn = {"Button.left": "left", "Button.right": "right",
               "Button.middle": "center"}.get(str(button), "other")
        _emit({"t": round(_t(), 4), "type": "down" if pressed else "up",
               "x": x, "y": y, "btn": btn, "count": 1})

    def on_scroll(x, y, dx, dy):
        _emit({"t": round(_t(), 4), "type": "scroll", "x": x, "y": y,
               "dx": dx, "dy": dy})

    def on_press(key):
        global _flags
        name = getattr(key, "name", None)
        if name and name in MOD_BITS:
            _flags |= MOD_BITS[name]
            _emit({"t": round(_t(), 4), "type": "mod", "flags": _flags, "code": -1})
            return
        if name:
            text = SPECIAL_NAMES.get(name, name)
            _emit({"t": round(_t(), 4), "type": "keydown", "code": -1,
                   "text": text, "flags": _flags, "repeat": 0})
        else:
            ch = getattr(key, "char", None) or ""
            _emit({"t": round(_t(), 4), "type": "keydown", "code": -1,
                   "text": ch, "flags": _flags, "repeat": 0})

    def on_release(key):
        global _flags
        name = getattr(key, "name", None)
        if name and name in MOD_BITS:
            _flags &= ~MOD_BITS[name]
            _emit({"t": round(_t(), 4), "type": "mod", "flags": _flags, "code": -1})
            return
        ch = getattr(key, "char", None) or SPECIAL_NAMES.get(name, name or "")
        _emit({"t": round(_t(), 4), "type": "keyup", "code": -1,
               "text": ch, "flags": _flags})

    def win_loop():
        while not _stop:
            _poll_windows(pin)
            time.sleep(0.1)

    ml = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
    kl = keyboard.Listener(on_press=on_press, on_release=on_release)
    ml.start()
    kl.start()
    threading.Thread(target=win_loop, daemon=True).start()
    _poll_windows(pin)
    _fp.flush()

    def _sig(_s, _f):
        global _stop
        _stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    sys.stderr.write("TAP READY\n")
    sys.stderr.flush()

    start = time.monotonic()
    while not _stop:
        time.sleep(0.1)
        if duration > 0 and (time.monotonic() - start) >= duration:
            break

    ml.stop()
    kl.stop()
    _emit({"t": round(_t(), 4), "type": "stop"})
    _fp.flush()
    _fp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
