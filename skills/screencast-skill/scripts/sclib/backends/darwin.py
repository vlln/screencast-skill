"""macOS backend: ffmpeg/avfoundation + pyobjc.

This is the verified path (macOS 27, Apple Silicon).
"""

import subprocess

NAME = "macos"
CAPTURE_IMPL = "avfoundation"


def _q():
    import Quartz
    return Quartz


def display_info(display_index=0):
    Q = _q()
    ids = Q.CGGetActiveDisplayList(16, None, None)[1]
    did = ids[min(display_index, len(ids) - 1)]
    b = Q.CGDisplayBounds(did)
    pw = int(Q.CGDisplayPixelsWide(did))
    ph = int(Q.CGDisplayPixelsHigh(did))
    pts_w, pts_h = float(b.size.width), float(b.size.height)
    return {
        "display_id": int(did),
        "origin": [float(b.origin.x), float(b.origin.y)],
        "points": [pts_w, pts_h],
        "pixels": [pw, ph],
        "scale": (pw / pts_w) if pts_w else 1.0,
    }


def list_windows():
    Q = _q()
    arr = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID)
    out = []
    for d in arr or []:
        if int(d.get("kCGWindowLayer", -1)) != 0:
            continue
        b = d.get("kCGWindowBounds") or {}
        out.append({
            "id": int(d.get("kCGWindowNumber", 0)),
            "owner": d.get("kCGWindowOwnerName", "") or "",
            "name": d.get("kCGWindowName", "") or "",
            "rect": [float(b.get("X", 0)), float(b.get("Y", 0)),
                     float(b.get("Width", 0)), float(b.get("Height", 0))],
        })
    return out


def audio_devices(ffmpeg):
    """(screens, audios) from `ffmpeg -f avfoundation -list_devices true`."""
    import re
    devs = subprocess.run([ffmpeg, "-hide_banner", "-f", "avfoundation",
                           "-list_devices", "true", "-i", ""],
                          capture_output=True, text=True).stderr
    screens, audios, seen_audio = [], [], False
    for ln in devs.splitlines():
        low = ln.lower()
        if "audio devices" in low:
            seen_audio = True
            continue
        if "video devices" in low:
            seen_audio = False
            continue
        m = re.search(r"\]\s*\[(\d+)\]\s+(.+)$", ln)
        if not m:
            continue
        (audios if seen_audio else screens).append(m.group(2).strip())
    return screens, audios


def resolve_audio(ffmpeg, spec):
    """--audio -> avfoundation device index (position in the device list)."""
    if spec is None or str(spec).strip() == "":
        return None
    spec = str(spec).strip()
    if spec.isdigit():
        return spec
    _, audios = audio_devices(ffmpeg)
    if not audios:
        raise SystemExit("no audio input devices found at all")
    low = spec.lower()
    if low == "mic":
        for i, a in enumerate(audios):
            if "mic" in a.lower():
                return str(i)
        raise SystemExit(
            "no microphone found. This Mac reports only: %s\n"
            "Desktop Macs (Mac Studio / Mac mini) have no built-in mic — plug "
            "one in, or use --audio system for loopback audio." % ", ".join(audios))
    if low in ("system", "loopback"):
        for i, a in enumerate(audios):
            if any(k in a.lower() for k in ("blackhole", "loopback", "soundflower", "aggregate")):
                return str(i)
        raise SystemExit(
            "no loopback device found. Install one and route output to it:\n"
            "    brew install blackhole-2ch\nthen create a Multi-Output Device "
            "in Audio MIDI Setup.")
    for i, a in enumerate(audios):
        if low in a.lower():
            return str(i)
    raise SystemExit("no audio device matching %r; available: %s" % (spec, ", ".join(audios)))


def capture_inputs(fps, display_index=0, audio_index=None, window=None):
    """ffmpeg input args. The whole display is captured; the renderer crops to
    the window rect from the recorded `win` timeline, so a window that moves is
    still followed."""
    inp = ["-f", "avfoundation",
           "-capture_cursor", "0", "-capture_mouse_clicks", "0",
           "-framerate", str(fps),
           "-i", "%d:%s" % (display_index, audio_index if audio_index is not None else "none")]
    return [inp]


def permissions():
    """(ok, {check: bool}, hints[]) — best effort, used by `rec doctor`."""
    hints = []
    checks = {}

    try:
        import ctypes
        AS = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        AS.AXIsProcessTrusted.restype = ctypes.c_bool
        checks["accessibility"] = bool(AS.AXIsProcessTrusted())
    except Exception:
        checks["accessibility"] = None

    probe = "/tmp/recd-probe.png"
    r = subprocess.run(["screencapture", "-x", "-R", "0,0,8,8", probe],
                       capture_output=True)
    checks["screen_recording"] = (r.returncode == 0)

    if not checks.get("accessibility"):
        hints.append("Accessibility denied — System Settings > Privacy & Security "
                     "> Accessibility, add your terminal and the python you run.")
    if not checks.get("screen_recording"):
        hints.append("Screen Recording denied — System Settings > Privacy & "
                     "Security > Screen Recording, add your terminal.")
    return all(v for v in checks.values() if v is not None), checks, hints


def events_module():
    return "sclib.events"


def drive_module():
    return "sclib.drive"
