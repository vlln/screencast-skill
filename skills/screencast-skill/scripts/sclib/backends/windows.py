"""Windows backend: ffmpeg/gdigrab + ctypes + pynput.

⚠️  UNVERIFIED ON WINDOWS. This module was written on macOS from the gdigrab
and pynput APIs. The macOS path is the one with test evidence. Expect to fix
details (DPI behaviour, dshow device strings) on first run; `rec doctor` reports
what it sees so the failure mode is diagnosable.

Design notes
  * Capture is the WHOLE desktop via gdigrab, same as macOS. The renderer then
    crops to the tracked window rect, so moving windows still work and there is
    only one code path for both targets.
  * `-draw_mouse 0` is gdigrab's equivalent of avfoundation's
    `-capture_cursor 0`: the pointer is excluded, and we draw our own.
  * Input uses pynput rather than raw user32 hooks: pynput's low-level hooks are
    far better tested than anything written blind here, and it keeps the
    recorder as small as the macOS one.
"""

import ctypes
import ctypes.wintypes as wt
import re
import subprocess

NAME = "windows"
CAPTURE_IMPL = "gdigrab"

user32 = ctypes.windll.user32

_dpi_done = False


def _enable_dpi():
    """Report physical pixels everywhere (gdigrab and pynput both do)."""
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def display_info(display_index=0):
    _enable_dpi()
    w = int(user32.GetSystemMetrics(0))
    h = int(user32.GetSystemMetrics(1))
    return {"display_id": 0, "origin": [0.0, 0.0],
            "points": [float(w), float(h)], "pixels": [w, h],
            "scale": 1.0}


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------
def _window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _process_name(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED = 0x1000
    try:
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return ""
        size = wt.DWORD(260)
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(h)
        return buf.value.rsplit("\\", 1)[-1]
    except Exception:
        return ""


def list_windows():
    _enable_dpi()
    out = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(hwnd)
        if not title:
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return True
        out.append({"id": int(hwnd), "owner": _process_name(hwnd),
                    "name": title,
                    "rect": [float(r.left), float(r.top), float(w), float(h)]})
        return True

    user32.EnumWindows(proc(cb), 0)

    fg = user32.GetForegroundWindow()
    out.sort(key=lambda w: 0 if w["id"] == int(fg) else 1)   # front first
    return out


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------
def audio_devices(ffmpeg):
    """(screens, audios) — dshow enumerates names, not indices."""
    try:
        devs = subprocess.run([ffmpeg, "-hide_banner", "-list_devices", "true",
                               "-f", "dshow", "-i", "dummy"],
                              capture_output=True, text=True).stderr
    except Exception:
        return [], []
    audios = []
    for ln in devs.splitlines():
        m = re.search(r'"(.+)"\s*\((audio|video)\)', ln)
        if m and m.group(2) == "audio":
            audios.append(m.group(1))
    return ["gdigrab desktop"], audios


def resolve_audio(ffmpeg, spec):
    """--audio -> a dshow device NAME (Windows has no stable indices)."""
    if spec is None or str(spec).strip() == "":
        return None
    spec = str(spec).strip()
    _, audios = audio_devices(ffmpeg)
    if not audios:
        raise SystemExit("no DirectShow audio device found (check `ffmpeg -list_devices true -f dshow -i dummy`)")
    low = spec.lower()
    if low == "mic":
        for a in audios:
            if "mic" in a.lower():
                return a
    if low in ("system", "loopback"):
        for a in audios:
            if "stereo mix" in a.lower() or "loopback" in a.lower() or "virtual" in a.lower():
                return a
        raise SystemExit(
            "no system-audio capture device. Enable 'Stereo Mix' in Sound "
            "settings, or install VB-Audio Virtual Cable and record that.")
    for a in audios:
        if low in a.lower() or spec == a:
            return a
    raise SystemExit("no audio device matching %r; available: %s" % (spec, ", ".join(audios)))


def capture_inputs(fps, display_index=0, audio_index=None, window=None):
    video = ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "0",
             "-i", "desktop"]
    inputs = [video]
    if audio_index:
        inputs.append(["-f", "dshow", "-i", "audio=%s" % audio_index])
    return inputs


def permissions():
    """gdigrab needs no TCC-style grant on Windows; input hooks need elevation
    only to see elevated apps."""
    hints = ["If events are empty while an admin app is focused, run your "
             "terminal elevated — Windows blocks low-level hooks across UIPI."]
    return True, {"platform": True}, hints


def events_module():
    return "sclib.events_win"


def drive_module():
    return "sclib.drive_win"
