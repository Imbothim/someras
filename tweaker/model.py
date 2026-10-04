"""The Tweak model and the runner that applies tweaks through the journal.

A :class:`Tweak` is a named, categorised bundle of :mod:`tweaker.actions`. It
carries a ``guard`` predicate (does this tweak apply to *this* machine?) and
metadata such as whether a reboot is needed and an honest note about what it
actually does.

The runner is the only place that writes the journal: for each action it
captures the before-state, records it, then applies — unless we're in dry-run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from . import actions as A
from .actions import Action
from .backend import Backend
from .journal import Journal, JournalEntry
from .sysinfo import SystemInfo


class Category(str, Enum):
    POWER = "power"
    INPUT = "input"
    GRAPHICS = "graphics"
    SYSTEM = "system"
    NETWORK = "network"
    BACKGROUND = "background"


class Risk(str, Enum):
    SAFE = "safe"               # reversible, no security cost
    SECURITY = "security"       # trades security/stability for speed
    EXPERIMENTAL = "experimental"  # effect varies per machine; A/B it
    PLACEBO = "placebo"         # included for completeness, little/no effect


@dataclass
class Tweak:
    id: str
    name: str
    category: Category
    actions: list[Action]
    risk: Risk = Risk.SAFE
    reboot: bool = False
    note: str = ""
    guard: Callable[[SystemInfo], bool] = field(default=lambda info: True)

    def applies_to(self, info: SystemInfo) -> bool:
        try:
            return bool(self.guard(info))
        except Exception:
            return False


class Status(str, Enum):
    APPLIED = "applied"
    SKIPPED = "skipped"       # guard not met
    UNCHANGED = "unchanged"   # already at desired state
    FAILED = "failed"
    PLANNED = "planned"       # dry-run only


@dataclass
class ActionOutcome:
    target: str
    status: Status
    detail: str = ""
    current: str = ""
    desired: str = ""


@dataclass
class TweakResult:
    tweak: Tweak
    status: Status
    outcomes: list[ActionOutcome] = field(default_factory=list)
    error: str = ""


@dataclass
class Runner:
    backend: Backend
    journal: Journal
    info: SystemInfo

    def apply_tweak(self, tweak: Tweak) -> TweakResult:
        if not tweak.applies_to(self.info):
            return TweakResult(tweak, Status.SKIPPED,
                               error="guard not met for this machine")

        outcomes: list[ActionOutcome] = []
        any_applied = False
        any_failed = False

        for action in tweak.actions:
            outcome = self._run_action(tweak, action)
            outcomes.append(outcome)
            if outcome.status == Status.APPLIED:
                any_applied = True
            elif outcome.status == Status.FAILED:
                any_failed = True

        if any_failed and not any_applied:
            status = Status.FAILED
        elif any_applied:
            status = Status.APPLIED
        elif self.backend.dry_run:
            status = Status.PLANNED
        else:
            status = Status.UNCHANGED
        return TweakResult(tweak, status, outcomes)

    def _run_action(self, tweak: Tweak, action: Action) -> ActionOutcome:
        target = action.target()
        try:
            before = action.capture(self.backend)
        except Exception as exc:  # capture failure: do not apply
            return ActionOutcome(target, Status.FAILED,
                                 detail=f"capture failed: {exc}")

        try:
            changed = action.is_changed(before)
        except Exception:
            changed = True

        current = desired = ""
        if self.backend.dry_run:
            try:
                current, desired = action.preview(self.backend)
            except Exception:
                pass
            status = Status.PLANNED if changed else Status.UNCHANGED
            return ActionOutcome(target, status, current=current, desired=desired)

        if not changed:
            return ActionOutcome(target, Status.UNCHANGED)

        # Record BEFORE applying, so a crash mid-apply is still revertible.
        self.journal.record(JournalEntry(
            action=action.action_name,
            target=target,
            before=before,
            tweak_id=tweak.id,
            note=tweak.note,
        ))
        try:
            self.journal.save()
        except Exception:
            pass  # best-effort; keep going

        try:
            action.apply(self.backend)
        except Exception as exc:
            return ActionOutcome(target, Status.FAILED, detail=f"apply failed: {exc}")
        return ActionOutcome(target, Status.APPLIED)


def revert_journal(backend: Backend, journal: Journal) -> list[ActionOutcome]:
    """Undo every entry in a journal, newest first."""
    outcomes: list[ActionOutcome] = []
    for entry in reversed(journal.entries):
        try:
            A.revert_entry(backend, entry.action, entry.before)
            outcomes.append(ActionOutcome(entry.target, Status.APPLIED,
                                          detail="reverted"))
        except Exception as exc:
            outcomes.append(ActionOutcome(entry.target, Status.FAILED,
                                          detail=f"revert failed: {exc}"))
    return outcomes
