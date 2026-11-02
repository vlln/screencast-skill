"""Windows scripted input (pynput), same script language as sclib.drive.

⚠️  UNVERIFIED ON WINDOWS.

    expect notepad.exe
    save
    move 700 400 800
    click
    type Hello
    key ctrl+a
    restore

`expect` accepts the process name (e.g. notepad.exe) or a window-title
substring, case-insensitively.
"""

import sys
import time

MODS = {"cmd": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt"}


def _frontmost():
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)

    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if h:
            size = wt.DWORD(260)
            pb = ctypes.create_unicode_buffer(260)
            ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, pb, ctypes.byref(size))
            ctypes.windll.kernel32.CloseHandle(h)
            exe = pb.value.rsplit("\\", 1)[-1]
    except Exception:
        pass
    return exe, buf.value


def main(argv):
    try:
        from pynput.keyboard import Controller as KeyController, Key
        from pynput.mouse import Button, Controller as MouseController
    except ImportError:
        sys.stderr.write("sclib.drive_win: pynput is required (pip install pynput)\n")
        return 1

    mouse = MouseController()
    kb = KeyController()
    cursor = [mouse.position[0], mouse.position[1]]
    saved = [None]

    def ease(t):
        return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2

    def move_to(x, y, ms):
        if ms <= 0:
            mouse.position = (int(x), int(y))
            cursor[0], cursor[1] = x, y
            return
        steps = max(2, int(ms / 8))
        ax, ay = cursor
        for i in range(1, steps + 1):
            p = ease(i / steps)
            mouse.position = (int(ax + (x - ax) * p), int(ay + (y - ay) * p))
            time.sleep(0.008)
        cursor[0], cursor[1] = x, y

    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if line.startswith("expect "):
            want = line[7:].strip().lower()
            exe, title = _frontmost()
            if want not in exe.lower() and want not in title.lower():
                sys.stderr.write("drive_win: ABORT expected %r, got %r / %r\n"
                                 % (want, exe, title))
                return 3
        elif line.strip() == "save":
            saved[0] = (cursor[0], cursor[1])
        elif line.strip() == "restore":
            if saved[0]:
                move_to(saved[0][0], saved[0][1], 220)
        elif line.startswith("move "):
            parts = line[5:].split()
            move_to(float(parts[0]), float(parts[1]),
                    int(parts[2]) if len(parts) > 2 else 400)
        elif line.strip() in ("click", "doubleclick"):
            n = 2 if line.strip() == "doubleclick" else 1
            for _ in range(n):
                mouse.press(Button.left)
                time.sleep(0.028)
                mouse.release(Button.left)
                time.sleep(0.040)
        elif line.startswith("type "):
            kb.type(line[5:])
        elif line.startswith("key "):
            spec = line[4:].strip()
            mods = []
            while True:
                if spec.startswith("cmd+"):
                    mods.append("ctrl"); spec = spec[4:]
                elif spec.startswith("ctrl+"):
                    mods.append("ctrl"); spec = spec[5:]
                elif spec.startswith("shift+"):
                    mods.append("shift"); spec = spec[6:]
                elif spec.startswith("alt+"):
                    mods.append("alt"); spec = spec[4:]
                else:
                    break
            keys = [getattr(Key, m) for m in mods if hasattr(Key, m)]
            for k in keys:
                kb.press(k)
            if spec:
                kb.press(spec[0])
                time.sleep(0.02)
                kb.release(spec[0])
            for k in reversed(keys):
                kb.release(k)
        elif line.startswith("sleep "):
            time.sleep(int(line[6:]) / 1000.0)
        else:
            sys.stderr.write("drive_win: unknown command %r\n" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
