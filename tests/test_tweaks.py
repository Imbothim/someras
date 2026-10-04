"""Whole tweak-set invariants and guard behaviour per machine profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from tweaker import actions as A
from tweaker.backend import Backend, FakeRegistry
from tweaker.journal import Journal
from tweaker.model import Runner, Status, revert_journal
from tweaker.sysinfo import SystemInfo
from tweaker.tweaks import build_all, by_id


def desktop_win11():
    return SystemInfo(
        is_windows=True, is_win11=True, windows_build=22631, is_laptop=False,
        gpu_vendor="nvidia", system_drive_ssd=True, ethernet=True,
        bitlocker_on=False, total_ram_gb=32,
        fortnite_dir=r"D:\FN",
        fortnite_exe=r"D:\FN\FortniteGame\Binaries\Win64"
                     r"\FortniteClient-Win64-Shipping.exe")


def laptop_win10_amd_hdd():
    return SystemInfo(
        is_windows=True, is_win11=False, windows_build=19045, is_laptop=True,
        gpu_vendor="amd", system_drive_ssd=False, ethernet=False,
        bitlocker_on=True, virtualization_in_use=True, total_ram_gb=8)


def test_ids_are_unique():
    tweaks = build_all(desktop_win11())
    ids = [t.id for t in tweaks]
    assert len(ids) == len(set(ids))


def test_every_action_is_revertible():
    for info in (desktop_win11(), laptop_win10_amd_hdd()):
        for tweak in build_all(info):
            for action in tweak.actions:
                assert action.action_name in A._REVERTERS, \
                    f"{tweak.id}: {action.action_name}"


def test_every_tweak_has_a_note():
    for tweak in build_all(desktop_win11()):
        assert tweak.note, f"{tweak.id} missing a note"


@pytest.mark.parametrize("builder", [desktop_win11, laptop_win10_amd_hdd])
def test_full_apply_then_revert_is_clean(tmp_path, builder):
    info = builder()
    reg = FakeRegistry()
    backend = Backend.fake(registry=reg)
    journal = Journal(path=tmp_path / "j.json")
    runner = Runner(backend, journal, info)

    results = [runner.apply_tweak(t) for t in build_all(info)]
    assert not any(r.status == Status.FAILED for r in results)

    outcomes = revert_journal(backend, journal)
    assert not any(o.status == Status.FAILED for o in outcomes)


def test_laptop_skips_idle_disable():
    assert by_id(laptop_win10_amd_hdd(), "power.idle_disable") is not None
    reg = FakeRegistry()
    backend = Backend.fake(registry=reg)
    journal = Journal(path=Path("/tmp/x.json"))
    runner = Runner(backend, journal, laptop_win10_amd_hdd())
    t = by_id(laptop_win10_amd_hdd(), "power.idle_disable")
    assert runner.apply_tweak(t).status == Status.SKIPPED


def test_win10_has_no_win11_only_tweaks():
    ids = {t.id for t in build_all(laptop_win10_amd_hdd())}
    # These tweaks are built unconditionally but guarded; guard must skip them.
    info = laptop_win10_amd_hdd()
    for tid in ("gfx.win11_swapchain", "gfx.global_timer_res"):
        t = by_id(info, tid)
        assert t is not None
        assert not t.applies_to(info)
    assert "gfx.display_max_hz" in ids  # non-win11-specific still present


def test_no_fortnite_omits_fortnite_tweaks():
    info = SystemInfo(is_windows=True, is_win11=True)  # no fortnite_exe
    ids = {t.id for t in build_all(info)}
    assert "gfx.fn_priority" not in ids
    assert "gfx.fn_gpu_pref" not in ids
    assert "net.qos_fortnite" not in ids
    assert "sys.defender_exclusion" not in ids


def test_fortnite_present_includes_fortnite_tweaks():
    ids = {t.id for t in build_all(desktop_win11())}
    assert "gfx.fn_priority" in ids
    assert "sys.defender_exclusion" in ids


def test_amd_gets_ulps_nvidia_gets_pstate():
    nvidia_ids = {t.id for t in build_all(desktop_win11())}
    assert "sys.nvidia_max_pstate" in nvidia_ids
    assert "sys.amd_ulps_off" not in nvidia_ids

    amd_ids = {t.id for t in build_all(laptop_win10_amd_hdd())}
    assert "sys.amd_ulps_off" in amd_ids
    assert "sys.nvidia_max_pstate" not in amd_ids


def test_bitlocker_adds_suspend_step():
    """When BitLocker is on, the bcd tweak suspends it first."""
    info = laptop_win10_amd_hdd()  # bitlocker_on=True
    bcd = by_id(info, "sys.bcd_timers")
    labels = [a.target() for a in bcd.actions]
    assert any("BitLocker" in label for label in labels)

    info2 = desktop_win11()  # bitlocker_on=False
    bcd2 = by_id(info2, "sys.bcd_timers")
    labels2 = [a.target() for a in bcd2.actions]
    assert not any("BitLocker" in label for label in labels2)


def test_hdd_keeps_sysmain_ssd_disables_it():
    assert by_id(laptop_win10_amd_hdd(), "bg.svc_sysmain") is None  # HDD
    assert by_id(desktop_win11(), "bg.svc_sysmain") is not None     # SSD
