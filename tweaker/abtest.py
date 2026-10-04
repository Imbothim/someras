"""A/B tester: prove which experimental tweaks actually help on THIS PC.

The whole point of the experimental tier is that these tweaks help on some
machines and do nothing (or hurt) on others. Guessing is how people end up with
a registry full of cargo-cult 'tweaks'. Instead, for each experimental tweak we
benchmark with it off, turn it on (rebooting if the tweak needs it), benchmark
again, and keep it only if the frame stats actually improved beyond measurement
noise.

The decision math is pure and unit-tested. Reboot-resume is handled with a small
JSON state file plus a RunOnce entry so the tool picks up where it left off.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .backend import Backend
from .benchmark import FrameStats

# A tweak must beat the baseline by more than this fraction to be "real" and not
# run-to-run noise. 1% lows naturally vary a few percent between runs.
NOISE_THRESHOLD = 0.03  # 3%


@dataclass
class Comparison:
    tweak_id: str
    before: FrameStats
    after: FrameStats

    @property
    def avg_delta_pct(self) -> float:
        if self.before.avg_fps <= 0:
            return 0.0
        return (self.after.avg_fps - self.before.avg_fps) / self.before.avg_fps

    @property
    def low_delta_pct(self) -> float:
        if self.before.low_1pct_fps <= 0:
            return 0.0
        return ((self.after.low_1pct_fps - self.before.low_1pct_fps)
                / self.before.low_1pct_fps)

    @property
    def spike_delta(self) -> int:
        return self.after.spikes - self.before.spikes


def is_improvement(cmp: Comparison,
                   threshold: float = NOISE_THRESHOLD) -> bool:
    """Keep a tweak only if it clearly helps and doesn't clearly hurt.

    Helps  = 1% low up > threshold, OR fewer spikes while avg didn't drop.
    Hurts  = average FPS down > threshold (overrides any 1%-low gain).
    """
    if cmp.avg_delta_pct < -threshold:
        return False  # regressed average FPS: reject outright
    if cmp.low_delta_pct > threshold:
        return True   # meaningfully better 1% lows
    if cmp.spike_delta < 0 and cmp.avg_delta_pct >= -threshold:
        return True   # fewer stutters, average held
    if cmp.avg_delta_pct > threshold:
        return True   # meaningfully better average
    return False      # within noise: not worth the risk


@dataclass
class ABState:
    """Persisted across reboots so the A/B run can resume."""

    pending: list[str] = field(default_factory=list)   # tweak ids left to test
    index: int = 0
    phase: str = "baseline"       # "baseline" | "applied"
    baseline: dict | None = None  # FrameStats as dict
    results: dict[str, bool] = field(default_factory=dict)  # tweak_id -> kept
    path: str = ""

    def save(self) -> None:
        if not self.path:
            return
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("path", None)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ABState | None":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        state = cls(**data)
        state.path = str(path)
        return state

    @property
    def current_tweak_id(self) -> str | None:
        if 0 <= self.index < len(self.pending):
            return self.pending[self.index]
        return None

    def advance(self) -> None:
        self.index += 1
        self.phase = "baseline"
        self.baseline = None


# --------------------------------------------------------------------------- #
# RunOnce resume (so the tool restarts itself after a reboot)
# --------------------------------------------------------------------------- #
RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_NAME = "FortniteTweakerABResume"


def schedule_resume(backend: Backend, extra_args: str = "--ab-resume") -> None:
    """Add a RunOnce entry that relaunches this tool after the next login."""
    from .backend import RegValue
    cmd = f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}" {extra_args}'
    backend.registry.write("HKCU", RUNONCE_KEY, RUNONCE_NAME,
                           RegValue(cmd, "REG_SZ"))


def clear_resume(backend: Backend) -> None:
    backend.registry.delete_value("HKCU", RUNONCE_KEY, RUNONCE_NAME)
