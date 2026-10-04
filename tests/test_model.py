"""Runner behaviour: apply, skip, unchanged, dry-run, journal ordering."""

from __future__ import annotations

from pathlib import Path

from tweaker import actions as A
from tweaker.backend import Backend, FakeRegistry
from tweaker.journal import Journal
from tweaker.model import (
    Category,
    Runner,
    Status,
    Tweak,
    revert_journal,
)
from tweaker.sysinfo import SystemInfo


def make_runner(tmp_path: Path, dry_run=False, info=None):
    reg = FakeRegistry()
    backend = Backend.fake(dry_run=dry_run, registry=reg)
    journal = Journal(path=tmp_path / "j.json")
    info = info or SystemInfo(is_windows=True)
    return Runner(backend, journal, info), backend, journal


def test_apply_records_journal_and_changes_value(tmp_path):
    runner, backend, journal = make_runner(tmp_path)
    t = Tweak("t", "T", Category.SYSTEM,
              [A.Reg("HKLM", r"S\K", "V", 1)])
    res = runner.apply_tweak(t)
    assert res.status == Status.APPLIED
    assert len(journal.entries) == 1
    assert backend.registry.read("HKLM", r"S\K", "V").data == 1


def test_guard_skips(tmp_path):
    runner, backend, journal = make_runner(tmp_path)
    t = Tweak("t", "T", Category.SYSTEM, [A.Reg("HKLM", r"S\K", "V", 1)],
              guard=lambda i: False)
    res = runner.apply_tweak(t)
    assert res.status == Status.SKIPPED
    assert len(journal.entries) == 0
    assert len(backend.registry.writes) == 0


def test_unchanged_when_already_set(tmp_path):
    runner, backend, journal = make_runner(tmp_path)
    backend.registry.seed("HKLM", r"S\K", "V", 1, "REG_DWORD")
    t = Tweak("t", "T", Category.SYSTEM, [A.Reg("HKLM", r"S\K", "V", 1)])
    res = runner.apply_tweak(t)
    assert res.status == Status.UNCHANGED
    assert len(journal.entries) == 0


def test_dry_run_makes_no_writes(tmp_path):
    runner, backend, journal = make_runner(tmp_path, dry_run=True)
    t = Tweak("t", "T", Category.SYSTEM, [A.Reg("HKLM", r"S\K", "V", 1)])
    res = runner.apply_tweak(t)
    assert res.status == Status.PLANNED
    assert len(backend.registry.writes) == 0
    assert len(journal.entries) == 0
    assert res.outcomes[0].desired == "1"


def test_journal_recorded_before_apply(tmp_path):
    """Even if apply fails, the before-state is journaled (crash safety)."""
    runner, backend, journal = make_runner(tmp_path)

    class Boom(A.Reg):
        def apply(self, backend):
            raise RuntimeError("boom")

    t = Tweak("t", "T", Category.SYSTEM, [Boom("HKLM", r"S\K", "V", 1)])
    res = runner.apply_tweak(t)
    assert res.status == Status.FAILED
    # The journal still has the entry so a later --revert can clean up.
    assert len(journal.entries) == 1


def test_revert_journal_restores_everything(tmp_path):
    runner, backend, journal = make_runner(tmp_path)
    backend.registry.seed("HKLM", r"S\K", "Old", 5, "REG_DWORD")
    runner.apply_tweak(Tweak("t", "T", Category.SYSTEM, [
        A.Reg("HKLM", r"S\K", "Old", 9),
        A.Reg("HKLM", r"S\K", "New", 1),
    ]))
    revert_journal(backend, journal)
    from tweaker.backend import MISSING
    assert backend.registry.read("HKLM", r"S\K", "Old").data == 5
    assert backend.registry.read("HKLM", r"S\K", "New") is MISSING


def test_partial_failure_still_reports_applied(tmp_path):
    runner, backend, journal = make_runner(tmp_path)

    class Boom(A.Reg):
        def apply(self, backend):
            raise RuntimeError("boom")

    t = Tweak("t", "T", Category.SYSTEM, [
        A.Reg("HKLM", r"S\K", "Good", 1),
        Boom("HKLM", r"S\K", "Bad", 1),
    ])
    res = runner.apply_tweak(t)
    # one applied, one failed -> overall APPLIED (best effort, never aborts)
    assert res.status == Status.APPLIED
    assert backend.registry.read("HKLM", r"S\K", "Good").data == 1
