"""Version string, read from VERSION next to the executable or source tree."""
import os
import sys


def _base():
    if getattr(sys, "frozen", False):
        # onefile build: bundled data lives in the temp extraction dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.exists(os.path.join(meipass, "VERSION")):
            return meipass
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def version():
    try:
        with open(os.path.join(_base(), "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"
