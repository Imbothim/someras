"""Power-plan and power-related tweaks.

Goal: stop Windows from ever down-clocking or parking anything while gaming.
The headline win is the Ultimate Performance plan plus no core parking and no
processor throttle floor, so the CPU stays at full clocks the instant a frame
needs it.
"""

from __future__ import annotations

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo

# Built-in "Ultimate Performance" scheme template GUID (present on Win10 1803+).
ULTIMATE_PERF = "e9a42b02-d5df-448d-aa00-03f14749eb61"

# powercfg subgroup / setting aliases.
SUB_PROCESSOR = "SUB_PROCESSOR"
SUB_PCIEXPRESS = "SUB_PCIEXPRESS"
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"

ASPM = "ee12f906-d277-404b-b6da-e5fa1a576df5"          # PCIe link state power mgmt
USB_SELECTIVE = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"  # USB selective suspend
PROCTHROTTLEMIN = "893dee8e-2bef-41e0-89c6-b55d0929964c"
CPMINCORES = "0cc5b647-c1df-4637-891a-dec35c318583"     # min cores (parking)


def build(info: SystemInfo) -> list[Tweak]:
    tweaks: list[Tweak] = [
        Tweak(
            id="power.ultimate",
            name="Activate Ultimate Performance power plan",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Highest-performance built-in plan; revert restores your old "
                 "plan.",
            actions=[A.PowerPlan(ULTIMATE_PERF)],
        ),
        Tweak(
            id="power.no_core_parking",
            name="Disable CPU core parking",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Keeps all cores awake (min cores = 100%).",
            actions=[A.PowerCfg(SUB_PROCESSOR, CPMINCORES, ac=100, dc=100,
                                label="CPU min cores 100%")],
        ),
        Tweak(
            id="power.proc_throttle_min",
            name="Set minimum processor state to 100%",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Stops the CPU from dropping below full speed.",
            actions=[A.PowerCfg(SUB_PROCESSOR, PROCTHROTTLEMIN, ac=100, dc=100,
                                label="Min processor state 100%")],
        ),
        Tweak(
            id="power.usb_no_suspend",
            name="Disable USB selective suspend",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Stops Windows briefly powering down USB devices (mouse/kbd).",
            actions=[A.PowerCfg(SUB_USB, USB_SELECTIVE, ac=0, dc=0,
                                label="USB selective suspend off")],
        ),
        Tweak(
            id="power.pcie_aspm_off",
            name="Disable PCIe link power management (ASPM)",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Keeps the GPU/NIC PCIe link at full power.",
            actions=[A.PowerCfg(SUB_PCIEXPRESS, ASPM, ac=0, dc=0,
                                label="PCIe ASPM off")],
        ),
        Tweak(
            id="power.throttling_off",
            name="Disable Windows power throttling (global)",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Turns off the OS from clamping background/foreground clocks.",
            actions=[A.Reg(
                "HKLM",
                r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling",
                "PowerThrottlingOff", 1)],
        ),
        Tweak(
            id="power.hibernate_off",
            name="Disable hibernation and Fast Startup",
            category=Category.POWER,
            risk=Risk.SAFE,
            note="Fast Startup causes a 'half-shutdown' state that leaves "
                 "drivers in a stale power mode; disabling gives clean boots.",
            actions=[A.Command(
                "powercfg -h off",
                apply_args=["powercfg", "-h", "off"],
                revert_args=["powercfg", "-h", "on"],
                desired="hibernate off")],
        ),
    ]

    # Desktop-only: hard-disable CPU idle so clocks never dip. On laptops this
    # wrecks battery and thermals, so it's guarded off.
    tweaks.append(Tweak(
        id="power.idle_disable",
        name="Disable CPU idle states (desktop only)",
        category=Category.POWER,
        risk=Risk.EXPERIMENTAL,
        note="Can REDUCE boost clocks on some modern CPUs (no C-states means "
             "no single-core boost headroom). A/B test this one.",
        guard=lambda i: not i.is_laptop,
        actions=[A.Command(
            "powercfg idle disable",
            apply_args=["powercfg", "-setacvalueindex", "SCHEME_CURRENT",
                        SUB_PROCESSOR,
                        "5d76a2ca-e8c0-402f-a133-2158492d58ad", "1"],
            revert_args=["powercfg", "-setacvalueindex", "SCHEME_CURRENT",
                         SUB_PROCESSOR,
                         "5d76a2ca-e8c0-402f-a133-2158492d58ad", "0"],
            desired="idle disabled")],
    ))
    return tweaks
