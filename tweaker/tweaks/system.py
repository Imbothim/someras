"""Security-for-speed trade-offs and low-level system tweaks.

Everything here is applied because the user asked for maximum performance and
accepted the trade-offs. Each item is reversible, and the end-of-run summary
lists exactly which protections were turned off. These are the tweaks most
likely to actually raise FPS on older hardware (VBS/mitigations), alongside a
set of obscure kernel/timer tweaks that the A/B tester can validate.
"""

from __future__ import annotations

import os

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo

DEVGUARD = r"SYSTEM\CurrentControlSet\Control\DeviceGuard"
HVCI = DEVGUARD + r"\Scenarios\HypervisorEnforcedCodeIntegrity"
MEMMGMT = (r"SYSTEM\CurrentControlSet\Control\Session Manager"
           r"\Memory Management")
KERNEL = r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel"


def build(info: SystemInfo) -> list[Tweak]:
    exe_dir = info.fortnite_dir
    exe = info.fortnite_exe
    tweaks: list[Tweak] = [
        # ---- Security trade-offs ---------------------------------------- #
        Tweak(
            id="sys.vbs_off",
            name="Disable Virtualization-Based Security / Memory Integrity",
            category=Category.SYSTEM,
            risk=Risk.SECURITY,
            reboot=True,
            note="SECURITY: removes HVCI. One of the biggest FPS wins on Win11, "
                 "but lowers exploit protection.",
            actions=[
                A.Reg("HKLM", DEVGUARD, "EnableVirtualizationBasedSecurity", 0),
                A.Reg("HKLM", HVCI, "Enabled", 0),
            ],
        ),
        Tweak(
            id="sys.vmplatform_off",
            name="Remove Virtual Machine Platform feature",
            category=Category.SYSTEM,
            risk=Risk.SECURITY,
            reboot=True,
            note="Skipped if WSL/Docker/Hyper-V is in use (would break them).",
            guard=lambda i: not i.virtualization_in_use,
            actions=[A.Command(
                "dism disable VirtualMachinePlatform",
                apply_args=["dism", "/online", "/disable-feature",
                            "/featurename:VirtualMachinePlatform", "/norestart"],
                revert_args=["dism", "/online", "/enable-feature",
                             "/featurename:VirtualMachinePlatform", "/norestart"],
                desired="VM Platform disabled")],
        ),
        Tweak(
            id="sys.mitigations_off",
            name="Disable Spectre/Meltdown CPU mitigations",
            category=Category.SYSTEM,
            risk=Risk.SECURITY,
            reboot=True,
            note="SECURITY: gain is mostly on older Intel CPUs; negligible on "
                 "recent hardware. Lowers protection against speculative "
                 "execution attacks.",
            actions=[
                A.Reg("HKLM", MEMMGMT, "FeatureSettingsOverride", 3),
                A.Reg("HKLM", MEMMGMT, "FeatureSettingsOverrideMask", 3),
            ],
        ),
        # ---- Low-level memory / filesystem ------------------------------ #
        Tweak(
            id="sys.disable_paging_exec",
            name="Keep kernel and drivers in RAM (DisablePagingExecutive)",
            category=Category.SYSTEM,
            risk=Risk.EXPERIMENTAL,
            reboot=True,
            note="Stops paging kernel code to disk. Helps on 16GB+ systems.",
            guard=lambda i: i.total_ram_gb == 0 or i.total_ram_gb >= 16,
            actions=[A.Reg("HKLM", MEMMGMT, "DisablePagingExecutive", 1)],
        ),
        Tweak(
            id="sys.fs_lastaccess_off",
            name="Disable NTFS last-access timestamps",
            category=Category.SYSTEM,
            risk=Risk.SAFE,
            note="Cuts a small disk write on every file read.",
            actions=[A.Command(
                "fsutil disablelastaccess",
                apply_args=["fsutil", "behavior", "set", "disablelastaccess", "1"],
                revert_args=["fsutil", "behavior", "set", "disablelastaccess", "2"],
                desired="last access off")],
        ),
        Tweak(
            id="sys.fs_8dot3_off",
            name="Disable 8.3 short filename creation",
            category=Category.SYSTEM,
            risk=Risk.SAFE,
            note="Minor NTFS write reduction on new files.",
            actions=[A.Command(
                "fsutil disable8dot3",
                apply_args=["fsutil", "behavior", "set", "disable8dot3", "1"],
                revert_args=["fsutil", "behavior", "set", "disable8dot3", "0"],
                desired="8.3 names off")],
        ),
        Tweak(
            id="sys.mem_compression_off",
            name="Disable memory compression (16GB+)",
            category=Category.SYSTEM,
            risk=Risk.EXPERIMENTAL,
            note="Trades RAM for CPU: with plenty of RAM, skipping compression "
                 "saves CPU cycles.",
            guard=lambda i: i.total_ram_gb == 0 or i.total_ram_gb >= 16,
            actions=[A.PowerShell(
                "Disable memory compression",
                apply_script="Disable-MMAgent -MemoryCompression",
                revert_script="Enable-MMAgent -MemoryCompression",
                desired="memory compression off")],
        ),
        # ---- Timers / scheduler (experimental) -------------------------- #
        Tweak(
            id="sys.distribute_timers",
            name="Distribute kernel timers across cores",
            category=Category.SYSTEM,
            risk=Risk.EXPERIMENTAL,
            reboot=True,
            note="Moves timer interrupts off core 0 so the game thread isn't "
                 "stalled by them.",
            actions=[A.Reg("HKLM", KERNEL, "DistributeTimers", 1)],
        ),
        Tweak(
            id="sys.mpo_off",
            name="Disable Multi-Plane Overlay (MPO)",
            category=Category.SYSTEM,
            risk=Risk.EXPERIMENTAL,
            reboot=True,
            note="Fixes flicker/stutter on some NVIDIA setups; no effect on "
                 "others.",
            actions=[A.Reg("HKLM",
                           r"SOFTWARE\Microsoft\Windows\Dwm",
                           "OverlayTestMode", 5)],
        ),
    ]

    # bcdedit timer tweaks. If BitLocker is on, suspend it for one reboot so we
    # don't trigger a recovery-key prompt.
    bcd_actions: list = []
    if info.bitlocker_on:
        sysdrive = os.environ.get("SystemDrive", "C:")
        bcd_actions.append(A.PowerShell(
            "Suspend BitLocker for one reboot",
            apply_script=f"Suspend-BitLocker -MountPoint '{sysdrive}' "
                         "-RebootCount 1",
            revert_script="",  # auto-resumes after the reboot
            desired="BitLocker suspended 1 boot"))
    bcd_actions += [
        A.Bcd("disabledynamictick", "yes", label="bcdedit disabledynamictick"),
        A.Bcd("useplatformtick", "yes", label="bcdedit useplatformtick"),
        A.Bcd("tscsyncpolicy", "enhanced", label="bcdedit tscsyncpolicy"),
    ]
    tweaks.append(Tweak(
        id="sys.bcd_timers",
        name="Tune boot timer configuration (bcdedit)",
        category=Category.SYSTEM,
        risk=Risk.EXPERIMENTAL,
        reboot=True,
        note="High-resolution platform timer + no dynamic tick. Effect varies "
             "by hardware; A/B test it. BitLocker handled automatically.",
        actions=bcd_actions,
    ))
    # Remove the legacy useplatformclock value (it HURTS on modern systems).
    tweaks.append(Tweak(
        id="sys.remove_platformclock",
        name="Remove forced platform clock (useplatformclock)",
        category=Category.SYSTEM,
        risk=Risk.SAFE,
        reboot=True,
        note="This old 'tweak' adds latency on modern PCs; we remove it if set.",
        actions=[A.Command(
            "bcdedit deletevalue useplatformclock",
            apply_args=["bcdedit", "/deletevalue", "{current}",
                        "useplatformclock"],
            revert_args=None,  # nothing to restore; it was harmful
            desired="useplatformclock removed")],
    ))

    # Defender exclusion for the Fortnite folder/process (performance only;
    # Defender stays ON for the rest of the system).
    if exe_dir:
        tweaks.append(Tweak(
            id="sys.defender_exclusion",
            name="Add Defender exclusion for the Fortnite folder",
            category=Category.SYSTEM,
            risk=Risk.SECURITY,
            note="SECURITY: real-time scanning won't touch Fortnite's files. "
                 "Defender still protects everything else.",
            actions=[A.PowerShell(
                "Add-MpPreference exclusion",
                apply_script=f"Add-MpPreference -ExclusionPath '{exe_dir}'; "
                             f"Add-MpPreference -ExclusionProcess "
                             f"'{os.path.basename(exe)}'",
                revert_script=f"Remove-MpPreference -ExclusionPath '{exe_dir}'; "
                              f"Remove-MpPreference -ExclusionProcess "
                              f"'{os.path.basename(exe)}'",
                desired="Fortnite folder excluded")],
        ))

    # Hidden/aggressive power settings (unhide, then set).
    tweaks += _hidden_power_tweaks(info)
    # GPU driver registry keys to hold max clocks.
    tweaks += _gpu_driver_tweaks(info)
    return tweaks


def _hidden_power_tweaks(info: SystemInfo) -> list[Tweak]:
    SUB_PROCESSOR = "SUB_PROCESSOR"
    PERFBOOSTMODE = "be337238-0d82-4146-a960-4f3749d470c7"
    PERFEPP = "36687f9e-e3a5-4dbf-b1dc-15eb381c6863"  # energy perf preference
    return [
        Tweak(
            id="sys.perf_boost_aggressive",
            name="Set processor performance boost mode to Aggressive",
            category=Category.POWER,
            risk=Risk.EXPERIMENTAL,
            note="Unhides and maxes CPU boost behaviour.",
            actions=[
                A.Command(
                    "unhide PERFBOOSTMODE",
                    apply_args=["powercfg", "-attributes", SUB_PROCESSOR,
                                PERFBOOSTMODE, "-ATTRIB_HIDE"],
                    revert_args=None,
                    desired="setting unhidden"),
                A.PowerCfg(SUB_PROCESSOR, PERFBOOSTMODE, ac=2, dc=2,
                           label="Boost mode = Aggressive"),
            ],
        ),
        Tweak(
            id="sys.perf_epp_zero",
            name="Set energy performance preference to 0 (full performance)",
            category=Category.POWER,
            risk=Risk.EXPERIMENTAL,
            note="Tells the CPU to always favour performance over efficiency.",
            actions=[
                A.Command(
                    "unhide PERFEPP",
                    apply_args=["powercfg", "-attributes", SUB_PROCESSOR,
                                PERFEPP, "-ATTRIB_HIDE"],
                    revert_args=None,
                    desired="setting unhidden"),
                A.PowerCfg(SUB_PROCESSOR, PERFEPP, ac=0, dc=0,
                           label="EPP = 0"),
            ],
        ),
    ]


def _gpu_driver_tweaks(info: SystemInfo) -> list[Tweak]:
    # Display adapter class key; the active adapter is usually \0000.
    base = (r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}\0000")
    out: list[Tweak] = []
    if info.gpu_vendor == "nvidia":
        out.append(Tweak(
            id="sys.nvidia_max_pstate",
            name="Keep NVIDIA GPU at maximum clocks (desktop only)",
            category=Category.GRAPHICS,
            risk=Risk.EXPERIMENTAL,
            reboot=True,
            note="Disables dynamic P-states. Runs hotter at idle; desktop only.",
            guard=lambda i: i.gpu_vendor == "nvidia" and not i.is_laptop,
            actions=[A.Reg("HKLM", base, "DisableDynamicPstate", 1)],
        ))
    elif info.gpu_vendor == "amd":
        out.append(Tweak(
            id="sys.amd_ulps_off",
            name="Disable AMD ULPS (ultra-low power state)",
            category=Category.GRAPHICS,
            risk=Risk.EXPERIMENTAL,
            reboot=True,
            note="Stops the GPU dropping to a low-power state under load.",
            guard=lambda i: i.gpu_vendor == "amd",
            actions=[A.Reg("HKLM", base, "EnableUlps", 0)],
        ))
    return out
