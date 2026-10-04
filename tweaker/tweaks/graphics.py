"""Graphics, GPU scheduling, and game-process priority tweaks.

These target the three things that actually move FPS and frame latency on
Windows: the compositor/capture overhead (Game DVR, fullscreen optimizations),
the GPU scheduler (HAGS), and how the OS schedules the game process and its
timer resolution.
"""

from __future__ import annotations

import os

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo

USER_GPU_PREFS = r"Software\Microsoft\DirectX\UserGpuPreferences"
MMCSS = (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia"
         r"\SystemProfile")
MMCSS_GAMES = MMCSS + r"\Tasks\Games"


def build(info: SystemInfo) -> list[Tweak]:
    exe = info.fortnite_exe
    exe_name = os.path.basename(exe) if exe else \
        "FortniteClient-Win64-Shipping.exe"
    has_fn = bool(exe)

    tweaks: list[Tweak] = [
        Tweak(
            id="gfx.game_mode",
            name="Enable Windows Game Mode",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="Lets Windows prioritise the foreground game.",
            actions=[A.Reg("HKCU", r"Software\Microsoft\GameBar",
                           "AutoGameModeEnabled", 1)],
        ),
        Tweak(
            id="gfx.gamedvr_off",
            name="Disable Game DVR / background recording",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="Background capture steals GPU time and adds frame latency.",
            actions=[
                A.Reg("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0),
                A.Reg("HKCU",
                      r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                      "AppCaptureEnabled", 0),
                A.Reg("HKLM",
                      r"SOFTWARE\Policies\Microsoft\Windows\GameDVR",
                      "AllowGameDVR", 0),
            ],
        ),
        Tweak(
            id="gfx.fse_behavior",
            name="Tune fullscreen optimizations globally",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="Sets the FSE behavior flags Fortnite benefits from.",
            actions=[
                A.Reg("HKCU", r"System\GameConfigStore",
                      "GameDVR_FSEBehaviorMode", 2),
                A.Reg("HKCU", r"System\GameConfigStore",
                      "GameDVR_FSEBehavior", 2),
                A.Reg("HKCU", r"System\GameConfigStore",
                      "GameDVR_HonorUserFSEBehaviorMode", 1),
                A.Reg("HKCU", r"System\GameConfigStore",
                      "GameDVR_DXGIHonorFSEWindowsCompatible", 1),
            ],
        ),
        Tweak(
            id="gfx.hags_on",
            name="Enable Hardware-Accelerated GPU Scheduling (HAGS)",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            reboot=True,
            note="Lets the GPU manage its own scheduling; pairs with Reflex.",
            actions=[A.Reg(
                "HKLM",
                r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "HwSchMode", 2)],
        ),
        Tweak(
            id="gfx.win11_swapchain",
            name="Enable optimizations for windowed games (Win11)",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            guard=lambda i: i.is_win11,
            note="Enables the newer flip-model swapchain for borderless.",
            actions=[A.RegMergePairs(
                "HKCU", USER_GPU_PREFS, "DirectXUserGlobalSettings",
                {"SwapEffectUpgradeEnable": "1"})],
        ),
        # MMCSS system responsiveness + games task priorities.
        Tweak(
            id="gfx.mmcss_responsiveness",
            name="Remove MMCSS reservation for background tasks",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="SystemResponsiveness=0 (Windows clamps to 10); frees CPU for "
                 "the game. NetworkThrottlingIndex off.",
            actions=[
                A.Reg("HKLM", MMCSS, "SystemResponsiveness", 0),
                A.Reg("HKLM", MMCSS, "NetworkThrottlingIndex", 0xFFFFFFFF),
            ],
        ),
        Tweak(
            id="gfx.mmcss_games",
            name="Raise MMCSS 'Games' task priorities",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="Gives the Games scheduling class top GPU/CPU priority.",
            actions=[
                A.Reg("HKLM", MMCSS_GAMES, "GPU Priority", 8),
                A.Reg("HKLM", MMCSS_GAMES, "Priority", 6),
                A.Reg("HKLM", MMCSS_GAMES, "Scheduling Category", "High",
                      "REG_SZ"),
                A.Reg("HKLM", MMCSS_GAMES, "SFIO Priority", "High", "REG_SZ"),
            ],
        ),
        Tweak(
            id="gfx.priority_separation",
            name="Tune Win32PrioritySeparation for foreground apps",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="0x26 favours the foreground game's threads. The A/B tester "
                 "can try 0x28 / 0x2A.",
            actions=[A.Reg(
                "HKLM",
                r"SYSTEM\CurrentControlSet\Control\PriorityControl",
                "Win32PrioritySeparation", 0x26)],
        ),
        Tweak(
            id="gfx.global_timer_res",
            name="Honor global timer resolution requests (Win11)",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            guard=lambda i: i.is_win11,
            note="Lets the session booster hold a high timer resolution "
                 "system-wide, not just for the requesting process.",
            actions=[A.Reg(
                "HKLM",
                r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel",
                "GlobalTimerResolutionRequests", 1)],
        ),
        Tweak(
            id="gfx.display_max_hz",
            name="Set primary display to its maximum refresh rate",
            category=Category.GRAPHICS,
            risk=Risk.SAFE,
            note="Huge, real latency win if your monitor was stuck below its "
                 "rated Hz.",
            actions=[A.DisplayRefresh()],
        ),
    ]

    # ---- Fortnite-specific (only if we found the install) ---------------- #
    if has_fn:
        tweaks += [
            Tweak(
                id="gfx.fn_gpu_pref",
                name="Pin Fortnite to the high-performance GPU",
                category=Category.GRAPHICS,
                risk=Risk.SAFE,
                note="GpuPreference=2 (discrete GPU). Matters on laptops/iGPU.",
                actions=[A.RegMergePairs(
                    "HKCU", USER_GPU_PREFS, exe, {"GpuPreference": "2"})],
            ),
            Tweak(
                id="gfx.fn_fso_disable",
                name="Disable fullscreen optimizations for Fortnite",
                category=Category.GRAPHICS,
                risk=Risk.SAFE,
                note="Compatibility layer flag for true exclusive fullscreen.",
                actions=[A.Reg(
                    "HKCU",
                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                    r"\AppCompatFlags\Layers",
                    exe, "~ DISABLEDXMAXIMIZEDWINDOWEDMODE HIGHDPIAWARE",
                    "REG_SZ")],
            ),
            Tweak(
                id="gfx.fn_priority",
                name="Run Fortnite at High CPU/IO priority",
                category=Category.GRAPHICS,
                risk=Risk.SAFE,
                note="IFEO PerfOptions: CpuPriorityClass=3 (High), IoPriority=3.",
                actions=[
                    A.Reg("HKLM",
                          r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                          r"\Image File Execution Options\\" + exe_name +
                          r"\PerfOptions",
                          "CpuPriorityClass", 3),
                    A.Reg("HKLM",
                          r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                          r"\Image File Execution Options\\" + exe_name +
                          r"\PerfOptions",
                          "IoPriority", 3),
                ],
            ),
        ]
    return tweaks
