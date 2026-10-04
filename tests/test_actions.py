"""Action capture/apply/revert correctness."""

from __future__ import annotations

from tweaker import actions as A
from tweaker.backend import MISSING, Backend, FakeRegistry, FakeRunner, CommandResult


def make_backend(dry_run=False):
    return Backend.fake(dry_run=dry_run, registry=FakeRegistry(),
                        runner=FakeRunner())


def test_reg_set_existing_captures_and_reverts():
    b = make_backend()
    b.registry.seed("HKLM", r"SYSTEM\X", "V", 5, "REG_DWORD")
    act = A.Reg("HKLM", r"SYSTEM\X", "V", 99, "REG_DWORD")
    before = act.capture(b)
    assert act.is_changed(before)
    act.apply(b)
    assert b.registry.read("HKLM", r"SYSTEM\X", "V").data == 99
    A.revert_entry(b, "reg", before)
    assert b.registry.read("HKLM", r"SYSTEM\X", "V").data == 5


def test_reg_set_missing_reverts_to_deleted():
    b = make_backend()
    act = A.Reg("HKLM", r"SYSTEM\X", "New", 1)
    before = act.capture(b)
    act.apply(b)
    assert b.registry.read("HKLM", r"SYSTEM\X", "New").data == 1
    A.revert_entry(b, "reg", before)
    assert b.registry.read("HKLM", r"SYSTEM\X", "New") is MISSING


def test_reg_unchanged_when_value_matches():
    b = make_backend()
    b.registry.seed("HKLM", r"S\K", "V", 2, "REG_DWORD")
    act = A.Reg("HKLM", r"S\K", "V", 2, "REG_DWORD")
    assert not act.is_changed(act.capture(b))


def test_reg_delete_recreates_on_revert():
    b = make_backend()
    b.registry.seed("HKLM", r"S\K", "Del", 7, "REG_DWORD")
    act = A.RegDelete("HKLM", r"S\K", "Del")
    before = act.capture(b)
    act.apply(b)
    assert b.registry.read("HKLM", r"S\K", "Del") is MISSING
    A.revert_entry(b, "reg", before)
    assert b.registry.read("HKLM", r"S\K", "Del").data == 7


def test_reg_merge_pairs_preserves_other_keys():
    b = make_backend()
    b.registry.seed("HKCU", r"S\Prefs", "App",
                    "GpuPreference=1;VRROptimizeEnable=1;", "REG_SZ")
    act = A.RegMergePairs("HKCU", r"S\Prefs", "App", {"GpuPreference": "2"})
    before = act.capture(b)
    assert act.is_changed(before)
    act.apply(b)
    result = b.registry.read("HKCU", r"S\Prefs", "App").data
    assert "GpuPreference=2;" in result
    assert "VRROptimizeEnable=1;" in result  # preserved
    A.revert_entry(b, "reg", before)
    assert b.registry.read("HKCU", r"S\Prefs", "App").data == \
        "GpuPreference=1;VRROptimizeEnable=1;"


def test_reg_merge_pairs_on_missing_value():
    b = make_backend()
    act = A.RegMergePairs("HKCU", r"S\Prefs", "App", {"GpuPreference": "2"})
    before = act.capture(b)
    act.apply(b)
    assert b.registry.read("HKCU", r"S\Prefs", "App").data == "GpuPreference=2;"
    A.revert_entry(b, "reg", before)
    assert b.registry.read("HKCU", r"S\Prefs", "App") is MISSING


def test_command_runs_apply_and_revert_args():
    runner = FakeRunner()
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.Command("x", apply_args=["foo", "on"], revert_args=["foo", "off"])
    before = act.capture(b)
    act.apply(b)
    assert ["foo", "on"] in runner.commands
    A.revert_entry(b, "command", before)
    assert ["foo", "off"] in runner.commands


def test_command_revert_none_is_noop():
    runner = FakeRunner()
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.Command("x", apply_args=["foo"], revert_args=None)
    before = act.capture(b)
    A.revert_entry(b, "command", before)  # should not raise
    assert runner.commands == []  # nothing run on revert


def test_powershell_apply_and_revert():
    runner = FakeRunner()
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.PowerShell("x", apply_script="Set-Thing", revert_script="Unset-Thing")
    before = act.capture(b)
    act.apply(b)
    assert "Set-Thing" in runner.scripts
    A.revert_entry(b, "powershell", before)
    assert "Unset-Thing" in runner.scripts


def test_powercfg_captures_current_index_and_reverts():
    runner = FakeRunner()
    runner.responses["/query"] = CommandResult(
        [], 0, "Current AC Power Setting Index: 0x0000000a\n"
               "Current DC Power Setting Index: 0x00000005\n", "")
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.PowerCfg("SUB", "SET", ac=100, dc=100)
    before = act.capture(b)
    assert before["ac"] == 10 and before["dc"] == 5
    act.apply(b)
    # applying sets the new value 100
    assert any("100" in " ".join(c) for c in runner.commands)
    A.revert_entry(b, "powercfg", before)
    # revert restores 10 / 5
    joined = [" ".join(c) for c in runner.commands]
    assert any(c.endswith("SUB SET 10") for c in joined)


def test_bcd_captures_previous_and_reverts_to_delete_when_unset():
    runner = FakeRunner()
    runner.responses["/enum"] = CommandResult([], 0, "identifier {current}\n", "")
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.Bcd("disabledynamictick", "yes")
    before = act.capture(b)
    assert before["previous"] is None
    A.revert_entry(b, "bcd", before)
    assert any("deletevalue" in " ".join(c) for c in runner.commands)


def test_bcd_reverts_to_previous_value():
    runner = FakeRunner()
    runner.responses["/enum"] = CommandResult(
        [], 0, "disabledynamictick       No\n", "")
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.Bcd("disabledynamictick", "yes")
    before = act.capture(b)
    assert before["previous"] == "No"
    A.revert_entry(b, "bcd", before)
    assert any(c[-2:] == ["disabledynamictick", "No"] for c in runner.commands)


def test_powerplan_reverts_to_previous_scheme():
    runner = FakeRunner()
    guid = "381b4222-f694-41f0-9685-ff5bb260df2e"
    runner.responses["/getactivescheme"] = CommandResult(
        [], 0, f"Power Scheme GUID: {guid}  (Balanced)\n", "")
    b = Backend.fake(registry=FakeRegistry(), runner=runner)
    act = A.PowerPlan("e9a42b02-d5df-448d-aa00-03f14749eb61")
    before = act.capture(b)
    assert before["previous"] == guid
    A.revert_entry(b, "powerplan", before)
    assert any(guid in " ".join(c) for c in runner.commands)


def test_every_action_name_has_a_reverter():
    for name in ("reg", "command", "powershell", "powercfg", "powerplan",
                 "bcd", "display"):
        assert name in A._REVERTERS


def test_unknown_reverter_raises():
    b = make_backend()
    try:
        A.revert_entry(b, "nonsense", {})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown action")
