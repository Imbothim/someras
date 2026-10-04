"""Live 'game session' booster that runs while Fortnite is open.

Some wins can't be made permanent in the registry — they only exist while a
process holds them. This module:

- holds a high timer resolution (0.5 ms) so Windows scheduling is fine-grained
  (pairs with the ``GlobalTimerResolutionRequests`` registry tweak on Win11)
- raises Fortnite's process priority and turns off power throttling for it
- gently lowers the priority of known background hogs (browsers, launchers)
- purges the standby memory list when free RAM runs low, preventing the
  texture-streaming stutter that comes from a full standby cache

Everything is restored when Fortnite exits (or the booster is stopped). On a
non-Windows box the booster degrades to a harmless no-op so it imports/tests.
"""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass, field

try:
    import psutil
except ImportError:  # keep importable without psutil
    psutil = None  # type: ignore

FORTNITE_PROC = "FortniteClient-Win64-Shipping.exe"

# Processes we temporarily de-prioritise while gaming (never killed).
BACKGROUND_HOGS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "discord.exe", "spotify.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "onedrive.exe", "searchindexer.exe", "msmpeng.exe",
}

# ntdll NtSetTimerResolution: resolution is in 100-ns units. 5000 = 0.5 ms.
TARGET_TIMER_100NS = 5000

# NtSetSystemInformation(SystemMemoryListInformation) command to purge standby.
SYSTEM_MEMORY_LIST_INFORMATION = 0x50
MEMORY_PURGE_STANDBY_LIST = 4


def _ntdll():
    try:
        return ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def set_timer_resolution(hundred_ns: int = TARGET_TIMER_100NS) -> bool:
    nt = _ntdll()
    if nt is None:
        return False
    current = ctypes.c_ulong(0)
    status = nt.NtSetTimerResolution(ctypes.c_ulong(hundred_ns), True,
                                     ctypes.byref(current))
    return status == 0


def reset_timer_resolution() -> None:
    nt = _ntdll()
    if nt is None:
        return
    current = ctypes.c_ulong(0)
    # Setting 'Set=False' releases our request.
    nt.NtSetTimerResolution(ctypes.c_ulong(TARGET_TIMER_100NS), False,
                            ctypes.byref(current))


def purge_standby_list() -> bool:
    """Free the standby memory cache (needs admin + SeProfileSingleProcess)."""
    nt = _ntdll()
    if nt is None:
        return False
    command = ctypes.c_int(MEMORY_PURGE_STANDBY_LIST)
    status = nt.NtSetSystemInformation(
        SYSTEM_MEMORY_LIST_INFORMATION,
        ctypes.byref(command),
        ctypes.sizeof(command),
    )
    return status == 0


def _find_fortnite():
    if psutil is None:
        return None
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == FORTNITE_PROC.lower():
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


@dataclass
class Booster:
    """Runs the live optimisations in a loop until Fortnite exits."""

    free_ram_purge_gb: float = 2.0     # purge standby when free RAM < this
    poll_seconds: float = 3.0
    demote_background: bool = True
    _saved_priorities: dict[int, int] = field(default_factory=dict)
    _running: bool = False

    def is_supported(self) -> bool:
        return os.name == "nt" and psutil is not None

    def _boost_fortnite(self, proc) -> None:
        try:
            proc.nice(psutil.HIGH_PRIORITY_CLASS)
        except (psutil.Error, OSError):
            pass
        # Turn off power throttling (EcoQoS) for the game process.
        try:
            _disable_power_throttling(proc.pid)
        except Exception:
            pass

    def _demote_hogs(self) -> None:
        if not self.demote_background:
            return
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = (proc.info["name"] or "").lower()
                if name in BACKGROUND_HOGS:
                    if proc.pid not in self._saved_priorities:
                        self._saved_priorities[proc.pid] = proc.nice()
                    proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except (psutil.Error, OSError):
                continue

    def _restore_hogs(self) -> None:
        for pid, prio in self._saved_priorities.items():
            try:
                psutil.Process(pid).nice(prio)
            except (psutil.Error, OSError):
                continue
        self._saved_priorities.clear()

    def run_once(self, proc) -> None:
        """One iteration of the maintenance loop."""
        set_timer_resolution()
        self._boost_fortnite(proc)
        self._demote_hogs()
        try:
            free_gb = psutil.virtual_memory().available / (1024 ** 3)
            if free_gb < self.free_ram_purge_gb:
                purge_standby_list()
        except Exception:
            pass

    def run(self, wait_for_launch: bool = True,
            on_event=lambda msg: None) -> None:
        """Block until Fortnite exits, optimising the whole time."""
        if not self.is_supported():
            on_event("Session booster needs Windows + psutil; skipping.")
            return

        self._running = True
        set_timer_resolution()
        try:
            proc = _find_fortnite()
            if proc is None and wait_for_launch:
                on_event("Waiting for Fortnite to launch...")
                while self._running and proc is None:
                    time.sleep(self.poll_seconds)
                    proc = _find_fortnite()
            if proc is None:
                on_event("Fortnite not running.")
                return
            on_event(f"Fortnite detected (pid {proc.pid}). Boosting...")
            while self._running and proc.is_running():
                self.run_once(proc)
                time.sleep(self.poll_seconds)
            on_event("Fortnite closed. Restoring background priorities.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self._restore_hogs()
        reset_timer_resolution()


# ----- Windows power-throttling toggle for a single process ---------------- #
# SetProcessInformation(ProcessPowerThrottling) with the mask cleared tells
# Windows never to put the game into EcoQoS/efficiency mode.
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ProcessPowerThrottling = 4
PROCESS_SET_INFORMATION = 0x0200


class _PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    ]


def _disable_power_throttling(pid: int) -> bool:
    if os.name != "nt":
        return False
    try:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    handle = k32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        state = _PROCESS_POWER_THROTTLING_STATE(
            PROCESS_POWER_THROTTLING_CURRENT_VERSION,
            PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
            0,  # StateMask 0 = throttling OFF for this process
        )
        ok = k32.SetProcessInformation(
            handle, ProcessPowerThrottling,
            ctypes.byref(state), ctypes.sizeof(state))
        return bool(ok)
    finally:
        k32.CloseHandle(handle)
