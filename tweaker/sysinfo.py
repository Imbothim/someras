"""Detect what kind of machine we're on, so guards can skip unsafe tweaks.

All detection is best-effort: if a probe fails we fall back to the safest
assumption (usually "unknown" / the value that makes a risky tweak skip). On
non-Windows the whole thing still constructs, which keeps tests simple.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .backend import MISSING, Backend


@dataclass
class SystemInfo:
    is_windows: bool = False
    windows_build: int = 0          # e.g. 19045 (Win10 22H2) or 22631 (Win11 23H2)
    is_win11: bool = False
    is_laptop: bool = False
    on_battery: bool = False
    gpu_vendor: str = "unknown"     # nvidia | amd | intel | unknown
    system_drive_ssd: bool = True
    ethernet: bool = True           # primary active adapter is wired
    bitlocker_on: bool = False
    virtualization_in_use: bool = False   # WSL2 / Hyper-V / Docker present
    cpu_hybrid: bool = False        # Intel 12th gen+ P/E cores
    total_ram_gb: int = 0
    fortnite_dir: str = ""
    fortnite_exe: str = ""

    # Convenience derived from above.
    @property
    def fortnite_installed(self) -> bool:
        return bool(self.fortnite_exe)


def _ps_value(backend: Backend, script: str) -> str:
    try:
        res = backend.runner.powershell(script)
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _reg_int(backend: Backend, hive: str, subkey: str, name: str) -> int | None:
    val = backend.registry.read(hive, subkey, name)
    if val is MISSING or isinstance(val.data, (bytes, bytearray)):
        return None
    try:
        return int(val.data)
    except (TypeError, ValueError):
        return None


def detect(backend: Backend) -> SystemInfo:
    info = SystemInfo()
    info.is_windows = os.name == "nt"
    if not info.is_windows:
        # On the dev box we stop here; callers that need real data guard on
        # is_windows anyway.
        return info

    _detect_windows_version(backend, info)
    _detect_power(backend, info)
    _detect_gpu(backend, info)
    _detect_storage(backend, info)
    _detect_network(backend, info)
    _detect_bitlocker(backend, info)
    _detect_virtualization(backend, info)
    _detect_cpu(backend, info)
    _detect_ram(backend, info)
    _detect_fortnite(backend, info)
    return info


def _detect_windows_version(backend: Backend, info: SystemInfo) -> None:
    sub = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    build = _reg_int(backend, "HKLM", sub, "CurrentBuildNumber")
    if build is None:
        val = backend.registry.read("HKLM", sub, "CurrentBuild")
        if val is not MISSING:
            try:
                build = int(val.data)
            except (TypeError, ValueError):
                build = 0
    info.windows_build = build or 0
    # Win11 is build 22000+.
    info.is_win11 = info.windows_build >= 22000


def _detect_power(backend: Backend, info: SystemInfo) -> None:
    # A desktop reports no battery; a laptop reports one.
    out = _ps_value(
        backend,
        "(Get-CimInstance Win32_Battery | Measure-Object).Count",
    )
    try:
        info.is_laptop = int(out) > 0
    except ValueError:
        info.is_laptop = False
    if info.is_laptop:
        status = _ps_value(
            backend,
            "(Get-CimInstance Win32_Battery).BatteryStatus",
        )
        # BatteryStatus 2 == on AC power.
        info.on_battery = status.strip() not in ("", "2")


def _detect_gpu(backend: Backend, info: SystemInfo) -> None:
    names = _ps_value(
        backend,
        "(Get-CimInstance Win32_VideoController).Name -join ';'",
    ).lower()
    if "nvidia" in names or "geforce" in names or "rtx" in names:
        info.gpu_vendor = "nvidia"
    elif "radeon" in names or "amd" in names:
        info.gpu_vendor = "amd"
    elif "intel" in names:
        info.gpu_vendor = "intel"


def _detect_storage(backend: Backend, info: SystemInfo) -> None:
    sysdrive = os.environ.get("SystemDrive", "C:").rstrip("\\")
    letter = sysdrive[:1]
    media = _ps_value(
        backend,
        "try { (Get-PhysicalDisk | Where-Object { "
        f"(Get-Partition -DriveLetter '{letter}' -ErrorAction SilentlyContinue | "
        "Get-Disk).Number -eq $_.DeviceId }).MediaType } catch { '' }",
    ).lower()
    if "hdd" in media:
        info.system_drive_ssd = False
    elif "ssd" in media:
        info.system_drive_ssd = True
    # Unknown -> keep default True (SysMain tweak only runs on SSD anyway).


def _detect_network(backend: Backend, info: SystemInfo) -> None:
    kind = _ps_value(
        backend,
        "(Get-NetConnectionProfile | Select-Object -First 1 "
        "-ExpandProperty InterfaceAlias)",
    ).lower()
    # Heuristic: Wi-Fi adapters usually contain 'wi-fi'/'wlan'/'wireless'.
    if any(w in kind for w in ("wi-fi", "wifi", "wlan", "wireless")):
        info.ethernet = False


def _detect_bitlocker(backend: Backend, info: SystemInfo) -> None:
    sysdrive = os.environ.get("SystemDrive", "C:")
    out = _ps_value(
        backend,
        "try { (Get-BitLockerVolume -MountPoint "
        f"'{sysdrive}').ProtectionStatus }} catch {{ 'Off' }}",
    )
    info.bitlocker_on = out.strip() in ("1", "On")


def _detect_virtualization(backend: Backend, info: SystemInfo) -> None:
    # WSL / Hyper-V / Docker all break if we remove Virtual Machine Platform.
    has_wsl = bool(shutil.which("wsl"))
    hyperv = _ps_value(
        backend,
        "(Get-CimInstance Win32_OptionalFeature -Filter "
        "\"Name='Microsoft-Hyper-V-All'\").InstallState",
    )
    docker = bool(shutil.which("docker"))
    info.virtualization_in_use = has_wsl or docker or hyperv.strip() == "1"


def _detect_cpu(backend: Backend, info: SystemInfo) -> None:
    name = _ps_value(
        backend,
        "(Get-CimInstance Win32_Processor | Select-Object -First 1 "
        "-ExpandProperty Name)",
    ).lower()
    # Intel 12th gen+ (Alder Lake and later) are hybrid P/E designs.
    info.cpu_hybrid = "intel" in name and any(
        tag in name for tag in ("i5-1", "i7-1", "i9-1", "ultra")
    )


def _detect_ram(backend: Backend, info: SystemInfo) -> None:
    out = _ps_value(
        backend,
        "[math]::Round((Get-CimInstance Win32_ComputerSystem)."
        "TotalPhysicalMemory / 1GB)",
    )
    try:
        info.total_ram_gb = int(float(out))
    except ValueError:
        info.total_ram_gb = 0


def _detect_fortnite(backend: Backend, info: SystemInfo) -> None:
    import json

    manifest = os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"),
        "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat",
    )
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for item in data.get("InstallationList", []):
        if item.get("AppName") == "Fortnite":
            loc = item.get("InstallLocation", "")
            if loc:
                info.fortnite_dir = loc
                info.fortnite_exe = os.path.join(
                    loc, "FortniteGame", "Binaries", "Win64",
                    "FortniteClient-Win64-Shipping.exe",
                )
            return
