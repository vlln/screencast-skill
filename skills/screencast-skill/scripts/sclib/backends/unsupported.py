"""Placeholder backend for platforms without an implementation yet."""

import sys

NAME = "unsupported"
CAPTURE_IMPL = None


def _die():
    raise SystemExit(
        "screencast-skill has no capture backend for %r yet.\n"
        "Implement sclib/backends/<platform>.py (see darwin.py / windows.py) "
        "with: display_info, list_windows, audio_devices, capture_inputs, "
        "permissions, events_module, drive_module." % sys.platform)


def display_info(display_index=0):
    _die()


def list_windows():
    return []


def audio_devices(ffmpeg):
    return [], []


def capture_inputs(fps, display_index=0, audio_index=None, window=None):
    _die()


def permissions():
    return False, {}, ["unsupported platform: %s" % sys.platform]


def events_module():
    _die()


def drive_module():
    _die()
