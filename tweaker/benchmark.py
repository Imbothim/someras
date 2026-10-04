"""Frame-time benchmarking via PresentMon.

Average FPS is the headline number, but the honest measure of 'smoothness' and
whether a tweak helped is the **1% low** (the average of the slowest 1% of
frames) and the frame-time spike count. This module shells out to PresentMon
(Intel's open-source frame-time tool; the user drops ``PresentMon.exe`` in
``tools/``) and computes those stats. The stats math is pure and unit-tested;
only the capture step needs Windows + PresentMon.
"""

from __future__ import annotations

import csv
import io
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

FORTNITE_PROC = "FortniteClient-Win64-Shipping.exe"


@dataclass
class FrameStats:
    frames: int
    avg_fps: float
    low_1pct_fps: float      # 1% low (99th-percentile frame time)
    low_01pct_fps: float     # 0.1% low
    spikes: int              # frames > 2x the median frame time
    duration_s: float

    def summary(self) -> str:
        return (f"{self.avg_fps:.0f} avg FPS | {self.low_1pct_fps:.0f} 1% low | "
                f"{self.low_01pct_fps:.0f} 0.1% low | {self.spikes} spikes "
                f"over {self.duration_s:.0f}s")


def _percentile_low_fps(frame_times_ms: list[float], pct: float) -> float:
    """1% low = mean FPS of the slowest `pct` fraction of frames."""
    if not frame_times_ms:
        return 0.0
    ordered = sorted(frame_times_ms, reverse=True)  # slowest first
    n = max(1, int(len(ordered) * pct))
    worst = ordered[:n]
    mean_ms = statistics.mean(worst)
    return 1000.0 / mean_ms if mean_ms > 0 else 0.0


def stats_from_frame_times(frame_times_ms: list[float]) -> FrameStats:
    frame_times_ms = [t for t in frame_times_ms if t > 0]
    if not frame_times_ms:
        return FrameStats(0, 0, 0, 0, 0, 0.0)
    total_s = sum(frame_times_ms) / 1000.0
    avg_fps = len(frame_times_ms) / total_s if total_s > 0 else 0.0
    median = statistics.median(frame_times_ms)
    spikes = sum(1 for t in frame_times_ms if t > 2 * median)
    return FrameStats(
        frames=len(frame_times_ms),
        avg_fps=avg_fps,
        low_1pct_fps=_percentile_low_fps(frame_times_ms, 0.01),
        low_01pct_fps=_percentile_low_fps(frame_times_ms, 0.001),
        spikes=spikes,
        duration_s=total_s,
    )


def parse_presentmon_csv(text: str) -> FrameStats:
    """Parse PresentMon CSV output into FrameStats.

    PresentMon column names vary by version; we look for a frame-time column
    (``msBetweenPresents`` on older versions, ``MsBetweenPresents`` / frame
    duration on newer ones).
    """
    reader = csv.DictReader(io.StringIO(text))
    frame_times: list[float] = []
    candidates = ("msBetweenPresents", "MsBetweenPresents",
                  "msUntilDisplayed", "FrameTime", "msBetweenDisplayChange")
    col = None
    for row in reader:
        if col is None:
            for c in candidates:
                if c in row:
                    col = c
                    break
            if col is None:
                break
        try:
            frame_times.append(float(row[col]))
        except (TypeError, ValueError):
            continue
    return stats_from_frame_times(frame_times)


def _presentmon_exe(tools_dir: Path) -> Path | None:
    for name in ("PresentMon.exe", "presentmon.exe"):
        p = tools_dir / name
        if p.exists():
            return p
    # Any PresentMon-*.exe
    hits = sorted(tools_dir.glob("PresentMon*.exe"))
    return hits[0] if hits else None


def capture(tools_dir: Path, seconds: int = 60,
            process_name: str = FORTNITE_PROC,
            out_dir: Path | None = None) -> FrameStats | None:
    """Run PresentMon for `seconds` against the game and return FrameStats.

    Returns None if PresentMon isn't available or the game isn't captured.
    """
    import subprocess

    exe = _presentmon_exe(tools_dir)
    if exe is None:
        return None
    out_dir = out_dir or tools_dir
    csv_path = out_dir / f"presentmon-{int(time.time())}.csv"
    args = [
        str(exe),
        "-process_name", process_name,
        "-timed", str(seconds),
        "-terminate_after_timed",
        "-no_top",
        "-output_file", str(csv_path),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True,
                       timeout=seconds + 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not csv_path.exists():
        return None
    try:
        return parse_presentmon_csv(csv_path.read_text(encoding="utf-8",
                                                       errors="ignore"))
    except OSError:
        return None
