"""System Restore point creation (Windows only).

A restore point is the big-hammer safety net; the per-change journal is the
precise one. We create a restore point only if the user says yes, bypassing the
default once-per-24h throttle for this one call and then putting that setting
back exactly as it was.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .backend import MISSING, Backend, RegValue

SR_KEY = (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore")
FREQ_VALUE = "SystemRestorePointCreationFrequency"


@dataclass
class RestoreResult:
    ok: bool
    message: str


def create_restore_point(backend: Backend,
                         description: str = "Before Fortnite Tweaker") \
        -> RestoreResult:
    if os.name != "nt":
        return RestoreResult(False, "restore points are Windows-only")

    runner = backend.runner
    reg = backend.registry

    # Ensure System Protection is enabled on the system drive.
    sysdrive = os.environ.get("SystemDrive", "C:") + "\\"
    runner.powershell(
        f"Enable-ComputerRestore -Drive '{sysdrive}'")

    # Temporarily remove the 24h throttle so this point is actually created.
    prev = reg.read("HKLM", SR_KEY, FREQ_VALUE)
    try:
        reg.write("HKLM", SR_KEY, FREQ_VALUE, RegValue(0, "REG_DWORD"))
    except Exception:
        pass

    try:
        res = runner.powershell(
            f"Checkpoint-Computer -Description '{description}' "
            "-RestorePointType 'MODIFY_SETTINGS'")
        created_ok = res.ok
        err = (res.stderr or "").strip()
    finally:
        # Restore the throttle setting exactly as it was.
        try:
            if prev is MISSING:
                reg.delete_value("HKLM", SR_KEY, FREQ_VALUE)
            else:
                reg.write("HKLM", SR_KEY, FREQ_VALUE, prev)
        except Exception:
            pass

    if not created_ok:
        return RestoreResult(
            False, f"Checkpoint-Computer failed: {err or 'unknown error'}")

    # Verify a point now exists.
    verify = runner.powershell(
        "(Get-ComputerRestorePoint | Measure-Object).Count")
    try:
        count = int((verify.stdout or "0").strip() or "0")
    except ValueError:
        count = 0
    if count > 0:
        return RestoreResult(True, f"Restore point created ({count} total).")
    return RestoreResult(True, "Restore point command succeeded "
                               "(could not verify count).")
