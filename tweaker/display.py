"""Primary-display refresh-rate control via ctypes (Windows only).

Running the monitor at its real maximum refresh rate is one of the biggest,
most reliable latency wins there is: a 240 Hz panel stuck at 60 Hz adds ~12 ms
of average display lag over running at 240 Hz. Everything here degrades to a
safe no-op / ``None`` when the Win32 display APIs aren't available (e.g. Linux,
headless), so the module imports anywhere.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x00000001
DISP_CHANGE_SUCCESSFUL = 0
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000


def _user32():
    try:
        return ctypes.windll.user32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", wintypes.SHORT),
        ("dmPaperSize", wintypes.SHORT),
        ("dmPaperLength", wintypes.SHORT),
        ("dmPaperWidth", wintypes.SHORT),
        ("dmScale", wintypes.SHORT),
        ("dmCopies", wintypes.SHORT),
        ("dmDefaultSource", wintypes.SHORT),
        ("dmPrintQuality", wintypes.SHORT),
        ("dmColor", wintypes.SHORT),
        ("dmDuplex", wintypes.SHORT),
        ("dmYResolution", wintypes.SHORT),
        ("dmTTOption", wintypes.SHORT),
        ("dmCollate", wintypes.SHORT),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def _new_devmode() -> DEVMODE:
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    return dm


def current_mode() -> tuple[int, int, int] | None:
    """Return ``(width, height, hz)`` of the primary display, or ``None``."""
    u = _user32()
    if u is None:
        return None
    dm = _new_devmode()
    if not u.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return (int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency))


def _modes_at_current_resolution() -> list[int]:
    """All refresh rates available at the current resolution."""
    u = _user32()
    cur = current_mode()
    if u is None or cur is None:
        return []
    width, height, _ = cur
    rates: set[int] = set()
    i = 0
    dm = _new_devmode()
    while u.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        if int(dm.dmPelsWidth) == width and int(dm.dmPelsHeight) == height:
            rates.add(int(dm.dmDisplayFrequency))
        i += 1
        dm = _new_devmode()
    return sorted(rates)


def max_refresh() -> int | None:
    """Highest refresh rate available at the current resolution."""
    rates = _modes_at_current_resolution()
    return rates[-1] if rates else None


def set_mode(width: int, height: int, hz: int) -> bool:
    """Set an exact mode. Returns True on success."""
    u = _user32()
    if u is None:
        return False
    dm = _new_devmode()
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmDisplayFrequency = hz
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    res = u.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None,
                                     CDS_UPDATEREGISTRY, None)
    return res == DISP_CHANGE_SUCCESSFUL


def set_max_refresh() -> bool:
    """Bump the primary display to its highest refresh at the same resolution."""
    cur = current_mode()
    best = max_refresh()
    if cur is None or best is None:
        return False
    if best == cur[2]:
        return True  # already maxed
    return set_mode(cur[0], cur[1], best)
