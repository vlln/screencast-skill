"""Platform backends.

The pipeline (facts -> plan -> render) is platform-independent: sclib/plan.py,
sclib/render.py and every layer contain zero OS-specific calls. Only three
things differ per OS, and they live here:

    display_info()      geometry + DPI/scale of a display
    list_windows()      capturable windows (id, owner, title, rect)
    capture_inputs()    ffmpeg input arguments for a take
    audio_devices()     input devices, for --audio resolution and doctor
    events_module()     module implementing the input/window recorder
    drive_module()      module implementing scripted input

Adding an OS means writing one module with this surface; nothing downstream
changes.
"""

import sys

if sys.platform == "darwin":
    from . import darwin as current
elif sys.platform.startswith("win"):
    from . import windows as current
else:
    from . import unsupported as current

__all__ = ["current"]
