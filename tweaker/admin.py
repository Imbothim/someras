"""Administrator detection and UAC self-elevation (Windows only)."""

from __future__ import annotations

import ctypes
import os
import sys


def is_admin() -> bool:
    """True if the current process is elevated. Non-Windows -> True (dev)."""
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def relaunch_elevated(extra_args: list[str] | None = None) -> bool:
    """Relaunch this script via UAC. Returns True if a relaunch was started.

    The caller should exit after this returns True, letting the elevated copy
    take over.
    """
    if os.name != "nt" or is_admin():
        return False
    args = list(sys.argv[1:]) + list(extra_args or [])
    params = " ".join(_quote(a) for a in [os.path.abspath(sys.argv[0]), *args])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", sys.executable, params, None, 1
        )
    except (AttributeError, OSError):
        return False
    # ShellExecuteW returns a value > 32 on success.
    return rc > 32


def _quote(arg: str) -> str:
    if not arg or any(c in arg for c in ' \t"'):
        return '"' + arg.replace('"', r"\"") + '"'
    return arg
