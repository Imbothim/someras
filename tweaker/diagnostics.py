"""Read-only diagnostics: region latency test and hardware warnings.

Nothing here changes the system. The region test measures TCP-connect round
trips to AWS endpoints near each Fortnite matchmaking region (Epic runs its
servers on AWS), which is a good proxy for in-game ping and, crucially, for
*jitter*. The hardware warnings surface the handful of things that cap your
performance but can only be fixed by you (BIOS, cables, hardware).
"""

from __future__ import annotations

import socket
import statistics
import time
from dataclasses import dataclass

from .backend import Backend
from .sysinfo import SystemInfo

# Fortnite matchmaking region -> a nearby AWS endpoint used only for timing.
# dynamodb.<region> answers fast on 443 and exists in every region.
REGION_ENDPOINTS: dict[str, str] = {
    "NA-East":     "dynamodb.us-east-1.amazonaws.com",
    "NA-West":     "dynamodb.us-west-1.amazonaws.com",
    "NA-Central":  "dynamodb.us-east-2.amazonaws.com",
    "Europe":      "dynamodb.eu-central-1.amazonaws.com",
    "Brazil":      "dynamodb.sa-east-1.amazonaws.com",
    "Oceania":     "dynamodb.ap-southeast-2.amazonaws.com",
    "Asia":        "dynamodb.ap-northeast-1.amazonaws.com",
    "Middle-East": "dynamodb.me-south-1.amazonaws.com",
}


@dataclass
class RegionResult:
    region: str
    host: str
    samples: list[float]        # milliseconds
    failures: int

    @property
    def ok(self) -> bool:
        return len(self.samples) > 0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.samples) if self.samples else float("inf")

    @property
    def jitter_ms(self) -> float:
        return statistics.pstdev(self.samples) if len(self.samples) > 1 else 0.0

    @property
    def best_ms(self) -> float:
        return min(self.samples) if self.samples else float("inf")


def _tcp_rtt(host: str, port: int = 443, timeout: float = 1.0) -> float | None:
    """One TCP-connect round trip in ms, or None on failure."""
    try:
        addr = (host, port)
        start = time.perf_counter()
        with socket.create_connection(addr, timeout=timeout):
            pass
        return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None


def ping_region(region: str, host: str, samples: int = 10) -> RegionResult:
    times: list[float] = []
    failures = 0
    for _ in range(samples):
        rtt = _tcp_rtt(host)
        if rtt is None:
            failures += 1
        else:
            times.append(rtt)
        time.sleep(0.05)
    return RegionResult(region, host, times, failures)


def ping_all(samples: int = 10,
             regions: dict[str, str] | None = None) -> list[RegionResult]:
    regions = regions or REGION_ENDPOINTS
    results = [ping_region(r, h, samples) for r, h in regions.items()]
    # Reachable first, sorted by average RTT.
    results.sort(key=lambda r: (not r.ok, r.avg_ms))
    return results


def best_region(results: list[RegionResult]) -> str | None:
    reachable = [r for r in results if r.ok]
    if not reachable:
        return None
    return min(reachable, key=lambda r: r.avg_ms).region


# --------------------------------------------------------------------------- #
# Hardware / configuration warnings (things only the user can fix)
# --------------------------------------------------------------------------- #
@dataclass
class Warning:
    title: str
    detail: str


def _ps(backend: Backend, script: str) -> str:
    try:
        return (backend.runner.powershell(script).stdout or "").strip()
    except Exception:
        return ""


def hardware_warnings(backend: Backend, info: SystemInfo) -> list[Warning]:
    warnings: list[Warning] = []

    if not info.ethernet:
        warnings.append(Warning(
            "You're on Wi-Fi",
            "Wi-Fi adds latency and jitter. A wired Ethernet cable is the "
            "single biggest real-world ping/stability improvement."))

    if info.on_battery:
        warnings.append(Warning(
            "Laptop running on battery",
            "Windows throttles hard on battery. Plug in for full performance."))

    # RAM running below its rated speed (XMP/EXPO not enabled).
    rated = _ps(backend,
                "(Get-CimInstance Win32_PhysicalMemory | "
                "Measure-Object -Property Speed -Maximum).Maximum")
    configured = _ps(backend,
                     "(Get-CimInstance Win32_PhysicalMemory | "
                     "Measure-Object -Property ConfiguredClockSpeed "
                     "-Maximum).Maximum")
    try:
        if rated and configured and int(configured) < int(rated):
            warnings.append(Warning(
                "RAM is running below its rated speed",
                f"Configured {configured} MHz vs rated {rated} MHz. Enable "
                "XMP/EXPO in BIOS for a free FPS boost (esp. 1% lows)."))
    except ValueError:
        pass

    # Single-channel memory.
    dimm_count = _ps(backend,
                     "(Get-CimInstance Win32_PhysicalMemory | "
                     "Measure-Object).Count")
    try:
        if dimm_count and int(dimm_count) == 1:
            warnings.append(Warning(
                "Single-channel RAM detected",
                "One stick = roughly half the memory bandwidth. Adding a "
                "matched second stick can noticeably raise FPS."))
    except ValueError:
        pass

    # GPU driver older than ~6 months.
    drv = _ps(backend,
              "$d=(Get-CimInstance Win32_VideoController | "
              "Sort-Object DriverDate -Descending | Select-Object -First 1)."
              "DriverDate; if ($d) { (New-TimeSpan -Start $d).Days }")
    try:
        if drv and int(drv) > 180:
            warnings.append(Warning(
                "GPU driver is old",
                f"Your graphics driver is ~{drv} days old. Update it — driver "
                "updates often include big Fortnite performance fixes."))
    except ValueError:
        pass

    # Fortnite on an HDD.
    if info.fortnite_dir:
        drive = info.fortnite_dir[:1]
        media = _ps(backend,
                    "try { (Get-PhysicalDisk | Where-Object { "
                    f"(Get-Partition -DriveLetter '{drive}' "
                    "-ErrorAction SilentlyContinue | Get-Disk).Number -eq "
                    "$_.DeviceId }).MediaType } catch { '' }")
        if "hdd" in media.lower():
            warnings.append(Warning(
                "Fortnite is installed on a hard drive",
                "Moving Fortnite to an SSD cuts texture streaming stutter and "
                "load times dramatically."))

    return warnings
