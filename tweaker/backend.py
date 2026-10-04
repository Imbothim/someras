"""Backends that actually touch the system, plus fakes for testing.

Everything that writes to the registry or runs a command goes through one of
these two interfaces. On Windows the real backend uses ``winreg`` and
``subprocess``. In tests (and on this Linux dev box) the fake backend keeps an
in-memory registry and records commands without running them, so the whole
tweak/journal/revert logic can be exercised without a Windows machine.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional, Protocol

# Registry hive names we accept in tweak definitions. Mapped to winreg constants
# lazily inside the real backend so this module imports fine on Linux.
HIVES = ("HKLM", "HKCU", "HKCR", "HKU", "HKCC")

# Registry value type names we use. Mapped to winreg REG_* constants lazily.
REG_SZ = "REG_SZ"
REG_DWORD = "REG_DWORD"
REG_QWORD = "REG_QWORD"
REG_BINARY = "REG_BINARY"
REG_MULTI_SZ = "REG_MULTI_SZ"
REG_EXPAND_SZ = "REG_EXPAND_SZ"


class MissingValue:
    """Sentinel meaning 'this registry value did not exist before'.

    Stored in the journal so revert knows to *delete* the value rather than
    writing some previous data back.
    """

    _instance: "MissingValue | None" = None

    def __new__(cls) -> "MissingValue":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


MISSING = MissingValue()


@dataclass
class RegValue:
    """A registry value's data plus its type."""

    data: object
    type: str = REG_DWORD


@dataclass
class CommandResult:
    args: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Registry(Protocol):
    def read(self, hive: str, subkey: str, name: str) -> RegValue | MissingValue: ...

    def write(self, hive: str, subkey: str, name: str, value: RegValue) -> None: ...

    def delete_value(self, hive: str, subkey: str, name: str) -> None: ...


class CommandRunner(Protocol):
    def run(self, args: list[str], *, check: bool = False) -> CommandResult: ...

    def powershell(self, script: str, *, check: bool = False) -> CommandResult: ...


# --------------------------------------------------------------------------- #
# Fake backend (tests + non-Windows dev)
# --------------------------------------------------------------------------- #
@dataclass
class FakeRegistry:
    """In-memory registry keyed by (hive, subkey_lower, name_lower)."""

    store: dict[tuple[str, str, str], RegValue] = field(default_factory=dict)
    reads: list[tuple[str, str, str]] = field(default_factory=list)
    writes: list[tuple[str, str, str, RegValue]] = field(default_factory=list)

    @staticmethod
    def _key(hive: str, subkey: str, name: str) -> tuple[str, str, str]:
        return (hive.upper(), subkey.lower().strip("\\"), name.lower())

    def seed(self, hive: str, subkey: str, name: str, data: object,
             type: str = REG_DWORD) -> None:
        self.store[self._key(hive, subkey, name)] = RegValue(data, type)

    def read(self, hive: str, subkey: str, name: str) -> RegValue | MissingValue:
        self.reads.append((hive, subkey, name))
        return self.store.get(self._key(hive, subkey, name), MISSING)

    def write(self, hive: str, subkey: str, name: str, value: RegValue) -> None:
        self.writes.append((hive, subkey, name, value))
        self.store[self._key(hive, subkey, name)] = value

    def delete_value(self, hive: str, subkey: str, name: str) -> None:
        self.writes.append((hive, subkey, name, RegValue(MISSING, "DELETE")))
        self.store.pop(self._key(hive, subkey, name), None)


@dataclass
class FakeRunner:
    """Records commands instead of running them. Canned responses optional."""

    commands: list[list[str]] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    responses: dict[str, CommandResult] = field(default_factory=dict)

    def _match(self, text: str) -> CommandResult | None:
        for needle, result in self.responses.items():
            if needle in text:
                return result
        return None

    def run(self, args: list[str], *, check: bool = False) -> CommandResult:
        self.commands.append(list(args))
        hit = self._match(" ".join(args))
        return hit or CommandResult(list(args), 0, "", "")

    def powershell(self, script: str, *, check: bool = False) -> CommandResult:
        self.scripts.append(script)
        hit = self._match(script)
        return hit or CommandResult(["powershell", script], 0, "", "")


# --------------------------------------------------------------------------- #
# Real Windows backend
# --------------------------------------------------------------------------- #
class RealRegistry:
    """winreg-backed registry. Imported lazily so this file loads on Linux."""

    def __init__(self) -> None:
        import winreg  # noqa: F401  (import-time check that we're on Windows)

        self._winreg = winreg
        self._hive_map = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT,
            "HKU": winreg.HKEY_USERS,
            "HKCC": winreg.HKEY_CURRENT_CONFIG,
        }
        self._type_map = {
            REG_SZ: winreg.REG_SZ,
            REG_DWORD: winreg.REG_DWORD,
            REG_QWORD: winreg.REG_QWORD,
            REG_BINARY: winreg.REG_BINARY,
            REG_MULTI_SZ: winreg.REG_MULTI_SZ,
            REG_EXPAND_SZ: winreg.REG_EXPAND_SZ,
        }
        self._type_rev = {v: k for k, v in self._type_map.items()}
        # 64-bit view so we don't land in the WOW6432Node redirector.
        self._access_read = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        self._access_write = winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY

    def read(self, hive: str, subkey: str, name: str) -> RegValue | MissingValue:
        wr = self._winreg
        root = self._hive_map[hive.upper()]
        try:
            with wr.OpenKey(root, subkey, 0, self._access_read) as key:
                data, rtype = wr.QueryValueEx(key, name)
        except FileNotFoundError:
            return MISSING
        return RegValue(data, self._type_rev.get(rtype, REG_SZ))

    def _open_or_create(self, hive: str, subkey: str):
        wr = self._winreg
        root = self._hive_map[hive.upper()]
        return wr.CreateKeyEx(root, subkey, 0, self._access_write)

    def write(self, hive: str, subkey: str, name: str, value: RegValue) -> None:
        wr = self._winreg
        rtype = self._type_map[value.type]
        with self._open_or_create(hive, subkey) as key:
            wr.SetValueEx(key, name, 0, rtype, value.data)

    def delete_value(self, hive: str, subkey: str, name: str) -> None:
        wr = self._winreg
        root = self._hive_map[hive.upper()]
        try:
            with wr.OpenKey(root, subkey, 0, self._access_write) as key:
                wr.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class RealRunner:
    """subprocess-backed command runner."""

    def run(self, args: list[str], *, check: bool = False) -> CommandResult:
        proc = subprocess.run(
            args, capture_output=True, text=True, shell=False,
        )
        result = CommandResult(list(args), proc.returncode,
                               proc.stdout or "", proc.stderr or "")
        if check and not result.ok:
            raise RuntimeError(
                f"command failed ({result.returncode}): {' '.join(args)}\n"
                f"{result.stderr}"
            )
        return result

    def powershell(self, script: str, *, check: bool = False) -> CommandResult:
        args = [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ]
        return self.run(args, check=check)


@dataclass
class Backend:
    """Bundle of a registry + a command runner, plus a dry-run flag."""

    registry: Registry
    runner: CommandRunner
    dry_run: bool = False

    @classmethod
    def real(cls, dry_run: bool = False) -> "Backend":
        return cls(RealRegistry(), RealRunner(), dry_run)

    @classmethod
    def fake(cls, dry_run: bool = False,
             registry: Optional[FakeRegistry] = None,
             runner: Optional[FakeRunner] = None) -> "Backend":
        return cls(registry or FakeRegistry(), runner or FakeRunner(), dry_run)
