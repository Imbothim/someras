"""The undo journal.

Before any action changes a value, it records the *before* state here. The
journal is written to disk so ``--revert`` works even in a brand new process
(e.g. after a reboot). Everything is plain JSON, with a couple of sentinels
encoded specially:

- a registry value that did not exist before is stored as ``{"__missing__": true}``
- registry binary data is stored as a hex string tagged with its type
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import MISSING, MissingValue, RegValue

JOURNAL_FORMAT = 1


def default_journal_dir() -> Path:
    """`%ProgramData%\\FortniteTweaker` on Windows, a temp dir elsewhere."""
    base = os.environ.get("ProgramData")
    if base:
        return Path(base) / "FortniteTweaker"
    # Dev / test fallback.
    return Path(os.environ.get("TMPDIR", "/tmp")) / "FortniteTweaker"


def _encode(value: Any) -> Any:
    if isinstance(value, MissingValue):
        return {"__missing__": True}
    if isinstance(value, RegValue):
        data = value.data
        if isinstance(data, (bytes, bytearray)):
            return {"__reg__": True, "type": value.type,
                    "hex": bytes(data).hex()}
        if isinstance(data, MissingValue):
            return {"__reg__": True, "type": value.type, "missing": True}
        return {"__reg__": True, "type": value.type, "data": data}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("__missing__"):
            return MISSING
        if value.get("__reg__"):
            if value.get("missing"):
                return RegValue(MISSING, value["type"])
            if "hex" in value:
                return RegValue(bytes.fromhex(value["hex"]), value["type"])
            return RegValue(value.get("data"), value["type"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


@dataclass
class JournalEntry:
    """One reversible change."""

    action: str          # e.g. "reg", "powercfg", "service"
    target: str          # human-readable location
    before: Any          # opaque payload for the action's revert()
    tweak_id: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "before": _encode(self.before),
            "tweak_id": self.tweak_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JournalEntry":
        return cls(
            action=d["action"],
            target=d["target"],
            before=_decode(d["before"]),
            tweak_id=d.get("tweak_id", ""),
            note=d.get("note", ""),
        )


@dataclass
class Journal:
    """A list of entries, loadable/savable as JSON."""

    path: Path
    entries: list[JournalEntry] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def record(self, entry: JournalEntry) -> None:
        self.entries.append(entry)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": JOURNAL_FORMAT,
            "created": self.created,
            "entries": [e.to_dict() for e in self.entries],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls, path: Path) -> "Journal":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        j = cls(path=Path(path), created=payload.get("created", time.time()))
        j.entries = [JournalEntry.from_dict(e) for e in payload.get("entries", [])]
        return j


def new_journal(directory: Path | None = None) -> Journal:
    directory = directory or default_journal_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Journal(path=directory / f"journal-{stamp}.json")


def latest_journal(directory: Path | None = None) -> Journal | None:
    directory = directory or default_journal_dir()
    if not directory.exists():
        return None
    files = sorted(directory.glob("journal-*.json"))
    if not files:
        return None
    return Journal.load(files[-1])
