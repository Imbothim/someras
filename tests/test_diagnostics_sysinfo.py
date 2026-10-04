"""Diagnostics math and sysinfo detection via the fake backend."""

from __future__ import annotations

from tweaker.backend import Backend, CommandResult, FakeRegistry, FakeRunner
from tweaker.diagnostics import RegionResult, best_region, hardware_warnings
from tweaker.sysinfo import SystemInfo, detect


def test_region_result_math():
    r = RegionResult("EU", "host", [10.0, 20.0, 30.0], failures=1)
    assert r.ok
    assert r.avg_ms == 20.0
    assert r.best_ms == 10.0
    assert r.jitter_ms > 0


def test_region_result_unreachable():
    r = RegionResult("EU", "host", [], failures=10)
    assert not r.ok
    assert r.avg_ms == float("inf")


def test_best_region_picks_lowest_avg():
    results = [
        RegionResult("A", "h", [50.0], 0),
        RegionResult("B", "h", [10.0], 0),
        RegionResult("C", "h", [], 5),
    ]
    assert best_region(results) == "B"


def test_best_region_none_when_all_unreachable():
    results = [RegionResult("A", "h", [], 5)]
    assert best_region(results) is None


def test_hardware_warnings_wifi_and_battery():
    backend = Backend.fake(registry=FakeRegistry(), runner=FakeRunner())
    info = SystemInfo(is_windows=True, ethernet=False, on_battery=True)
    titles = [w.title for w in hardware_warnings(backend, info)]
    assert any("Wi-Fi" in t for t in titles)
    assert any("battery" in t for t in titles)


def test_hardware_warnings_ram_below_rated():
    runner = FakeRunner()
    # rated 6000, configured 4800 -> below rated
    runner.responses["-Property Speed -Maximum"] = CommandResult(
        [], 0, "6000", "")
    runner.responses["ConfiguredClockSpeed -Maximum"] = CommandResult(
        [], 0, "4800", "")
    backend = Backend.fake(registry=FakeRegistry(), runner=runner)
    info = SystemInfo(is_windows=True, ethernet=True)
    titles = [w.title for w in hardware_warnings(backend, info)]
    assert any("rated speed" in t for t in titles)


def test_detect_non_windows_is_safe():
    # On the dev box os.name != 'nt' so detect short-circuits.
    backend = Backend.fake(registry=FakeRegistry(), runner=FakeRunner())
    info = detect(backend)
    assert isinstance(info, SystemInfo)
