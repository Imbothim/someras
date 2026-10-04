"""The in-game settings checklist.

We deliberately do NOT edit Fortnite's config files (ban risk is near zero for
editing an ini, but the user asked for Windows-only changes, and these settings
are things you want to see and understand yourself). These are the settings that
actually dominate input latency — far more than any registry tweak.
"""

from __future__ import annotations

from .sysinfo import SystemInfo

GENERIC = [
    ("Window Mode", "Fullscreen (exclusive) — lowest latency, not Borderless"),
    ("VSync", "Off — VSync adds a full frame of input lag"),
    ("Frame Rate Limit", "Set to your monitor's Hz, or uncapped if your FPS "
                         "stays comfortably above it"),
    ("Rendering Mode", "Performance Mode for max FPS on weaker CPUs, or DX12 "
                       "on strong GPUs"),
    ("Anti-Aliasing / Effects / Post", "Low/Off for competitive clarity + FPS"),
    ("View Distance", "Epic (players render at distance regardless, but keep "
                      "FPS stable)"),
    ("3D Resolution", "100% — lowering it blurs enemies"),
]

NVIDIA = ("NVIDIA Reflex Low Latency", "ON + BOOST — the biggest single latency "
          "reduction available in-game")
AMD = ("AMD Anti-Lag (in Adrenalin)", "ON — reduces input latency")


def lines(info: SystemInfo, best_region: str | None = None) -> list[str]:
    out = ["In-game settings to set yourself (these matter most for 'feel'):"]
    for name, rec in GENERIC:
        out.append(f"  - {name}: {rec}")
    if info.gpu_vendor == "nvidia":
        out.append(f"  - {NVIDIA[0]}: {NVIDIA[1]}")
    elif info.gpu_vendor == "amd":
        out.append(f"  - {AMD[0]}: {AMD[1]}")
    else:
        out.append(f"  - {NVIDIA[0]}: {NVIDIA[1]} (if you have an NVIDIA GPU)")
    if best_region:
        out.append(f"  - Matchmaking Region: {best_region} "
                   "(lowest ping in the test above)")
    else:
        out.append("  - Matchmaking Region: pick the one with the lowest ping "
                   "in Fortnite's region selector")
    out.append("  - Network: use a wired Ethernet cable; enable SQM/'bufferbloat "
               "fix' on your router to kill lag spikes while downloading")
    return out
