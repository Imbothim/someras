"""Collect every tweak from every category module.

``build_all(info)`` returns the full, ordered list of tweaks for *this* machine.
Guards still run later (in the Runner), but building with ``info`` lets modules
include machine-specific actions (Fortnite exe path, Win10 vs Win11 keys, etc.).
"""

from __future__ import annotations

from ..model import Tweak
from ..sysinfo import SystemInfo
from . import background, graphics, input, network, power, system

# Order matters for the summary and for apply order (power plan first so the
# powercfg value tweaks land in the right scheme).
_MODULES = [power, input, graphics, system, network, background]


def build_all(info: SystemInfo) -> list[Tweak]:
    tweaks: list[Tweak] = []
    seen: set[str] = set()
    for module in _MODULES:
        for tweak in module.build(info):
            if tweak.id in seen:
                raise ValueError(f"duplicate tweak id: {tweak.id}")
            seen.add(tweak.id)
            tweaks.append(tweak)
    return tweaks


def by_id(info: SystemInfo, tweak_id: str) -> Tweak | None:
    for tweak in build_all(info):
        if tweak.id == tweak_id:
            return tweak
    return None
