"""Reversible actions.

An *action* is the smallest unit of change. Every action can:

- ``capture(backend)``  — read the current state and return a payload
- ``is_changed(before)`` — is the system already at the desired state?
- ``preview(backend)``  — ``(current, desired)`` strings for ``--dry-run``
- ``apply(backend)``    — perform the change
- ``revert(backend, before)`` — put the captured state back

Actions never write to the journal themselves; the Tweak runner captures the
``before`` payload, stores it, and only then applies. Revert after a reboot is
driven entirely by the journal: :func:`revert_entry` dispatches on the stored
action name, so no live action object is required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .backend import Backend, MissingValue, RegValue


class Action:
    action_name = "base"

    def target(self) -> str:
        raise NotImplementedError

    def capture(self, backend: Backend) -> Any:
        raise NotImplementedError

    def is_changed(self, before: Any) -> bool:
        """True if applying would actually change something."""
        return True

    def preview(self, backend: Backend) -> tuple[str, str]:
        return ("?", "?")

    def apply(self, backend: Backend) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass
class Reg(Action):
    """Set a registry value (creating the key/value if needed)."""

    hive: str
    subkey: str
    name: str
    data: object
    type: str = "REG_DWORD"

    def __post_init__(self) -> None:
        self.action_name = "reg"

    def target(self) -> str:
        label = self.name_str or "(default)"
        return f"{self.hive}\\{self.subkey}\\{label}"

    @property
    def name_str(self) -> str:
        return self.name if isinstance(self.name, str) else str(self.name)

    def capture(self, backend: Backend) -> dict:
        before = backend.registry.read(self.hive, self.subkey, self.name_str)
        return {
            "hive": self.hive,
            "subkey": self.subkey,
            "name": self.name_str,
            "value": before,
        }

    def is_changed(self, before: dict) -> bool:
        val = before["value"]
        if isinstance(val, MissingValue):
            return True
        return not (val.data == self.data and val.type == self.type)

    def preview(self, backend: Backend) -> tuple[str, str]:
        val = backend.registry.read(self.hive, self.subkey, self.name_str)
        cur = "(missing)" if isinstance(val, MissingValue) else _fmt(val.data)
        return (cur, _fmt(self.data))

    def apply(self, backend: Backend) -> None:
        backend.registry.write(
            self.hive, self.subkey, self.name_str, RegValue(self.data, self.type)
        )


@dataclass
class RegDelete(Action):
    """Delete a registry value (revert recreates it if it existed)."""

    hive: str
    subkey: str
    name: str

    def __post_init__(self) -> None:
        self.action_name = "reg"

    def target(self) -> str:
        return f"{self.hive}\\{self.subkey}\\{self.name}"

    def capture(self, backend: Backend) -> dict:
        before = backend.registry.read(self.hive, self.subkey, self.name)
        return {
            "hive": self.hive,
            "subkey": self.subkey,
            "name": self.name,
            "value": before,
        }

    def is_changed(self, before: dict) -> bool:
        return not isinstance(before["value"], MissingValue)

    def preview(self, backend: Backend) -> tuple[str, str]:
        val = backend.registry.read(self.hive, self.subkey, self.name)
        cur = "(missing)" if isinstance(val, MissingValue) else _fmt(val.data)
        return (cur, "(deleted)")

    def apply(self, backend: Backend) -> None:
        backend.registry.delete_value(self.hive, self.subkey, self.name)


@dataclass
class RegMergePairs(Action):
    """Merge ``key=value;`` pairs into a REG_SZ value, preserving other pairs.

    Windows stores GPU/DirectX per-app preferences as a single string like
    ``GpuPreference=2;VRROptimizeEnable=1;``. Overwriting it would wipe the
    other settings, so this reads the current string, updates only the keys we
    care about, and writes it back. Revert restores the exact original string
    (handled by the standard reg reverter).
    """

    hive: str
    subkey: str
    name: str
    pairs: dict[str, str]

    def __post_init__(self) -> None:
        self.action_name = "reg"

    def target(self) -> str:
        label = self.name or "(default)"
        return f"{self.hive}\\{self.subkey}\\{label}"

    def capture(self, backend: Backend) -> dict:
        before = backend.registry.read(self.hive, self.subkey, self.name)
        return {"hive": self.hive, "subkey": self.subkey, "name": self.name,
                "value": before}

    @staticmethod
    def _parse(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for chunk in text.split(";"):
            chunk = chunk.strip()
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _merged(self, backend: Backend) -> str:
        before = backend.registry.read(self.hive, self.subkey, self.name)
        current = "" if isinstance(before, MissingValue) else str(before.data)
        pairs = self._parse(current)
        pairs.update(self.pairs)
        return "".join(f"{k}={v};" for k, v in pairs.items())

    def is_changed(self, before: dict) -> bool:
        val = before["value"]
        current = "" if isinstance(val, MissingValue) else str(val.data)
        pairs = self._parse(current)
        return any(pairs.get(k) != v for k, v in self.pairs.items())

    def preview(self, backend: Backend) -> tuple[str, str]:
        val = backend.registry.read(self.hive, self.subkey, self.name)
        cur = "(missing)" if isinstance(val, MissingValue) else str(val.data)
        return (cur, self._merged(backend))

    def apply(self, backend: Backend) -> None:
        backend.registry.write(self.hive, self.subkey, self.name,
                               RegValue(self._merged(backend), "REG_SZ"))


def _revert_reg(backend: Backend, before: dict) -> None:
    val = before["value"]
    if isinstance(val, MissingValue):
        backend.registry.delete_value(before["hive"], before["subkey"],
                                      before["name"])
    else:
        backend.registry.write(before["hive"], before["subkey"],
                               before["name"], val)


# --------------------------------------------------------------------------- #
# Generic command with an explicit inverse
# --------------------------------------------------------------------------- #
@dataclass
class Command(Action):
    """Run a command whose inverse command is known up front.

    ``label`` is shown to the user. ``apply_args`` runs on apply; ``revert_args``
    (stored in the journal) runs on revert. Use for schtasks enable/disable,
    netsh toggles, etc. Not for anything where the previous value must be read
    first — use :class:`CapturedCommand` for that.
    """

    label: str
    apply_args: list[str]
    revert_args: list[str] | None = None
    desired: str = ""

    def __post_init__(self) -> None:
        self.action_name = "command"

    def target(self) -> str:
        return self.label

    def capture(self, backend: Backend) -> dict:
        return {"label": self.label, "revert_args": self.revert_args}

    def preview(self, backend: Backend) -> tuple[str, str]:
        return ("(command)", self.desired or " ".join(self.apply_args))

    def apply(self, backend: Backend) -> None:
        backend.runner.run(self.apply_args)


def _revert_command(backend: Backend, before: dict) -> None:
    args = before.get("revert_args")
    if args:
        backend.runner.run(args)


@dataclass
class PowerShell(Action):
    """Run a PowerShell apply script with a known revert script."""

    label: str
    apply_script: str
    revert_script: str | None = None
    desired: str = ""

    def __post_init__(self) -> None:
        self.action_name = "powershell"

    def target(self) -> str:
        return self.label

    def capture(self, backend: Backend) -> dict:
        return {"label": self.label, "revert_script": self.revert_script}

    def preview(self, backend: Backend) -> tuple[str, str]:
        return ("(powershell)", self.desired or self.apply_script)

    def apply(self, backend: Backend) -> None:
        backend.runner.powershell(self.apply_script)


def _revert_powershell(backend: Backend, before: dict) -> None:
    script = before.get("revert_script")
    if script:
        backend.runner.powershell(script)


# --------------------------------------------------------------------------- #
# powercfg setting inside the active scheme
# --------------------------------------------------------------------------- #
@dataclass
class PowerCfg(Action):
    """Set one powercfg value in the active scheme, capturing the old index.

    ``sub`` and ``setting`` are the subgroup/setting GUIDs or aliases.
    ``ac`` and ``dc`` are the new values (ints). The old AC value is read with
    ``powercfg /query`` so revert can restore it.
    """

    sub: str
    setting: str
    ac: int
    dc: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        self.action_name = "powercfg"

    def target(self) -> str:
        return self.label or f"powercfg {self.sub} {self.setting}"

    def _query_current(self, backend: Backend) -> tuple[int | None, int | None]:
        res = backend.runner.run(
            ["powercfg", "/query", "SCHEME_CURRENT", self.sub, self.setting]
        )
        ac = dc = None
        for line in res.stdout.splitlines():
            m = re.search(r"Current AC Power Setting Index:\s*(0x[0-9a-fA-F]+)",
                          line)
            if m:
                ac = int(m.group(1), 16)
            m = re.search(r"Current DC Power Setting Index:\s*(0x[0-9a-fA-F]+)",
                          line)
            if m:
                dc = int(m.group(1), 16)
        return ac, dc

    def capture(self, backend: Backend) -> dict:
        ac, dc = self._query_current(backend)
        return {"sub": self.sub, "setting": self.setting, "ac": ac, "dc": dc}

    def preview(self, backend: Backend) -> tuple[str, str]:
        ac, _ = self._query_current(backend)
        cur = "?" if ac is None else str(ac)
        return (cur, str(self.ac))

    def apply(self, backend: Backend) -> None:
        backend.runner.run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
                             self.sub, self.setting, str(self.ac)])
        if self.dc is not None:
            backend.runner.run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT",
                                self.sub, self.setting, str(self.dc)])
        backend.runner.run(["powercfg", "/setactive", "SCHEME_CURRENT"])


def _revert_powercfg(backend: Backend, before: dict) -> None:
    ac = before.get("ac")
    dc = before.get("dc")
    if ac is not None:
        backend.runner.run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
                            before["sub"], before["setting"], str(ac)])
    if dc is not None:
        backend.runner.run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT",
                            before["sub"], before["setting"], str(dc)])
    backend.runner.run(["powercfg", "/setactive", "SCHEME_CURRENT"])


# --------------------------------------------------------------------------- #
# Active power scheme switch (Ultimate Performance)
# --------------------------------------------------------------------------- #
@dataclass
class PowerPlan(Action):
    """Duplicate a built-in scheme and make it active.

    Captures the currently active scheme GUID. On revert, re-activates the old
    scheme and deletes the scheme we created.
    """

    template_guid: str
    label: str = "Ultimate Performance power plan"

    def __post_init__(self) -> None:
        self.action_name = "powerplan"

    def target(self) -> str:
        return self.label

    def _active_guid(self, backend: Backend) -> str | None:
        res = backend.runner.run(["powercfg", "/getactivescheme"])
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", res.stdout)
        return m.group(1) if m else None

    def capture(self, backend: Backend) -> dict:
        return {"previous": self._active_guid(backend), "created": None}

    def preview(self, backend: Backend) -> tuple[str, str]:
        return (self._active_guid(backend) or "?", "Ultimate Performance")

    def apply(self, backend: Backend) -> None:
        res = backend.runner.run(["powercfg", "/duplicatescheme",
                                  self.template_guid])
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", res.stdout)
        new_guid = m.group(1) if m else self.template_guid
        backend.runner.run(["powercfg", "/setactive", new_guid])


def _revert_powerplan(backend: Backend, before: dict) -> None:
    prev = before.get("previous")
    if prev:
        backend.runner.run(["powercfg", "/setactive", prev])


# --------------------------------------------------------------------------- #
# bcdedit value
# --------------------------------------------------------------------------- #
@dataclass
class Bcd(Action):
    """Set a bcdedit value, capturing the previous value from ``/enum``."""

    key: str
    value: str
    label: str = ""

    def __post_init__(self) -> None:
        self.action_name = "bcd"

    def target(self) -> str:
        return self.label or f"bcdedit {self.key}"

    def _current(self, backend: Backend) -> str | None:
        res = backend.runner.run(["bcdedit", "/enum", "{current}"])
        for line in res.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == self.key.lower():
                return parts[1].strip()
        return None

    def capture(self, backend: Backend) -> dict:
        return {"key": self.key, "previous": self._current(backend)}

    def preview(self, backend: Backend) -> tuple[str, str]:
        return (self._current(backend) or "(unset)", self.value)

    def apply(self, backend: Backend) -> None:
        backend.runner.run(["bcdedit", "/set", "{current}", self.key, self.value])


def _revert_bcd(backend: Backend, before: dict) -> None:
    prev = before.get("previous")
    key = before["key"]
    if prev is None:
        backend.runner.run(["bcdedit", "/deletevalue", "{current}", key])
    else:
        backend.runner.run(["bcdedit", "/set", "{current}", key, prev])


# --------------------------------------------------------------------------- #
# Display refresh rate
# --------------------------------------------------------------------------- #
@dataclass
class DisplayRefresh(Action):
    """Set the primary display to its highest available refresh rate.

    The heavy lifting lives in :mod:`tweaker.display` (ctypes, Windows only).
    Captured ``before`` is the current refresh rate so revert restores it.
    """

    label: str = "Primary display refresh rate -> max"

    def __post_init__(self) -> None:
        self.action_name = "display"

    def target(self) -> str:
        return self.label

    def _current(self, backend: Backend) -> tuple[int, int, int] | None:
        from . import display
        return display.current_mode()

    def capture(self, backend: Backend) -> dict:
        cur = self._current(backend)
        return {"mode": cur}

    def preview(self, backend: Backend) -> tuple[str, str]:
        from . import display
        cur = self._current(backend)
        best = display.max_refresh()
        cur_s = f"{cur[2]} Hz" if cur else "?"
        best_s = f"{best} Hz" if best else "?"
        return (cur_s, best_s)

    def apply(self, backend: Backend) -> None:
        from . import display
        display.set_max_refresh()


def _revert_display(backend: Backend, before: dict) -> None:
    mode = before.get("mode")
    if mode:
        from . import display
        display.set_mode(*mode)


# --------------------------------------------------------------------------- #
# Revert dispatch (journal-driven)
# --------------------------------------------------------------------------- #
_REVERTERS = {
    "reg": _revert_reg,
    "command": _revert_command,
    "powershell": _revert_powershell,
    "powercfg": _revert_powercfg,
    "powerplan": _revert_powerplan,
    "bcd": _revert_bcd,
    "display": _revert_display,
}


def revert_entry(backend: Backend, action_name: str, before: Any) -> None:
    reverter = _REVERTERS.get(action_name)
    if reverter is None:
        raise ValueError(f"no reverter registered for action '{action_name}'")
    reverter(backend, before)


def _fmt(data: object) -> str:
    if isinstance(data, (bytes, bytearray)):
        return "0x" + bytes(data).hex()
    return str(data)
