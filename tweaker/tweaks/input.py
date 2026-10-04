"""Mouse and keyboard tweaks for a direct, consistent feel.

Mouse acceleration ("Enhance pointer precision") is the big one: with it off,
the same physical mouse movement always maps to the same on-screen movement, so
flick shots become muscle-memory instead of a guess.
"""

from __future__ import annotations

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo


def build(info: SystemInfo) -> list[Tweak]:
    return [
        Tweak(
            id="input.mouse_accel_off",
            name="Disable mouse acceleration (Enhance pointer precision)",
            category=Category.INPUT,
            risk=Risk.SAFE,
            note="1:1 mouse movement. The single most important input tweak.",
            actions=[
                A.Reg("HKCU", r"Control Panel\Mouse", "MouseSpeed", "0",
                      "REG_SZ"),
                A.Reg("HKCU", r"Control Panel\Mouse", "MouseThreshold1", "0",
                      "REG_SZ"),
                A.Reg("HKCU", r"Control Panel\Mouse", "MouseThreshold2", "0",
                      "REG_SZ"),
            ],
        ),
        Tweak(
            id="input.fast_key_repeat",
            name="Fastest keyboard repeat rate and shortest delay",
            category=Category.INPUT,
            risk=Risk.SAFE,
            note="Snappier key repeat (affects menus/chat, not gameplay polling).",
            actions=[
                A.Reg("HKCU", r"Control Panel\Keyboard", "KeyboardDelay", "0",
                      "REG_SZ"),
                A.Reg("HKCU", r"Control Panel\Keyboard", "KeyboardSpeed", "31",
                      "REG_SZ"),
            ],
        ),
        Tweak(
            id="input.sticky_keys_off",
            name="Disable Sticky / Filter / Toggle Keys popups",
            category=Category.INPUT,
            risk=Risk.SAFE,
            note="Stops the accessibility hotkeys that can interrupt a game "
                 "(e.g. tapping Shift 5x).",
            actions=[
                A.Reg("HKCU", r"Control Panel\Accessibility\StickyKeys",
                      "Flags", "506", "REG_SZ"),
                A.Reg("HKCU", r"Control Panel\Accessibility\Keyboard Response",
                      "Flags", "122", "REG_SZ"),
                A.Reg("HKCU", r"Control Panel\Accessibility\ToggleKeys",
                      "Flags", "58", "REG_SZ"),
            ],
        ),
        Tweak(
            id="input.mouse_queue",
            name="Raise mouse/keyboard data queue size",
            category=Category.INPUT,
            risk=Risk.EXPERIMENTAL,
            note="Moderate buffer bump (kept conservative: too high can DROP "
                 "inputs at 1000Hz+ polling).",
            actions=[
                A.Reg("HKLM",
                      r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters",
                      "MouseDataQueueSize", 50),
                A.Reg("HKLM",
                      r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters",
                      "KeyboardDataQueueSize", 50),
            ],
        ),
    ]
