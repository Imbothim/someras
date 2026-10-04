"""Reduce background CPU/GPU/disk/network contention while gaming.

These free up resources that would otherwise be spent on telemetry, background
apps, visual effects, and indexing — the stuff that causes 1% low / frame-time
spikes rather than lowering average FPS.
"""

from __future__ import annotations

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo

SERVICES = r"SYSTEM\CurrentControlSet\Services"

# Telemetry / maintenance scheduled tasks safe to disable.
TELEMETRY_TASKS = [
    r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    r"\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
]


def _service_disable(svc: str, label: str) -> Tweak:
    """Disable a service by setting its Start value to 4 (the reg is journaled)."""
    return Tweak(
        id=f"bg.svc_{svc.lower()}",
        name=f"Disable service: {label}",
        category=Category.BACKGROUND,
        risk=Risk.SAFE,
        note=f"{label} is not needed for gaming; revert restores its start type.",
        actions=[A.Reg("HKLM", rf"{SERVICES}\{svc}", "Start", 4)],
    )


def build(info: SystemInfo) -> list[Tweak]:
    tweaks: list[Tweak] = [
        Tweak(
            id="bg.apps_off",
            name="Disable background apps",
            category=Category.BACKGROUND,
            risk=Risk.SAFE,
            note="Stops UWP/Store apps running when you're not using them.",
            actions=(
                [A.Reg("HKLM",
                       r"SOFTWARE\Policies\Microsoft\Windows\AppPrivacy",
                       "LetAppsRunInBackground", 2)]
                if info.is_win11 else
                [A.Reg("HKCU",
                       r"Software\Microsoft\Windows\CurrentVersion"
                       r"\BackgroundAccessApplications",
                       "GlobalUserDisabled", 1)]
            ),
        ),
        Tweak(
            id="bg.visual_fx",
            name="Set visual effects to best performance",
            category=Category.BACKGROUND,
            risk=Risk.SAFE,
            note="Turns off animations/shadows that cost GPU/CPU on the desktop.",
            actions=[
                A.Reg("HKCU",
                      r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                      r"\VisualEffects",
                      "VisualFXSetting", 2),
                A.Reg("HKCU",
                      r"Software\Microsoft\Windows\DWM", "EnableAeroPeek", 0),
            ],
        ),
        Tweak(
            id="bg.transparency_off",
            name="Disable transparency effects",
            category=Category.BACKGROUND,
            risk=Risk.SAFE,
            note="Small, constant DWM GPU saving.",
            actions=[A.Reg("HKCU",
                           r"Software\Microsoft\Windows\CurrentVersion\Themes"
                           r"\Personalize",
                           "EnableTransparency", 0)],
        ),
        # Core telemetry/maintenance services.
        _service_disable("DiagTrack", "Connected User Experiences & Telemetry"),
        _service_disable("dmwappushservice", "WAP Push Message Routing"),
        _service_disable("MapsBroker", "Downloaded Maps Manager"),
        _service_disable("RetailDemo", "Retail Demo Service"),
        Tweak(
            id="bg.telemetry_tasks",
            name="Disable telemetry scheduled tasks",
            category=Category.BACKGROUND,
            risk=Risk.SAFE,
            note="Stops CEIP/compatibility appraiser tasks running in the "
                 "background.",
            actions=[
                A.Command(
                    f"schtasks disable {t.rsplit(chr(92), 1)[-1]}",
                    apply_args=["schtasks", "/Change", "/TN", t, "/Disable"],
                    revert_args=["schtasks", "/Change", "/TN", t, "/Enable"],
                    desired="task disabled")
                for t in TELEMETRY_TASKS
            ],
        ),
    ]

    # SysMain (Superfetch) only on SSDs — on an HDD it genuinely helps load
    # times, so we leave it alone there.
    if info.system_drive_ssd:
        tweaks.append(_service_disable("SysMain", "SysMain / Superfetch (SSD)"))

    return tweaks
