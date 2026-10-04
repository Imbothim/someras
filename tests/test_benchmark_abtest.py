"""Benchmark stats math and A/B decision logic."""

from __future__ import annotations

from tweaker.abtest import ABState, Comparison, is_improvement
from tweaker.benchmark import (
    FrameStats,
    parse_presentmon_csv,
    stats_from_frame_times,
)


def test_stats_basic_fps():
    # 100 frames at exactly 10ms -> 100 FPS, 10s.
    stats = stats_from_frame_times([10.0] * 100)
    assert round(stats.avg_fps) == 100
    assert round(stats.duration_s) == 1  # 100 * 10ms = 1s


def test_stats_empty():
    stats = stats_from_frame_times([])
    assert stats.frames == 0 and stats.avg_fps == 0


def test_one_percent_low_is_worse_than_average():
    # mostly fast frames, a few slow ones
    frames = [4.0] * 990 + [40.0] * 10
    stats = stats_from_frame_times(frames)
    assert stats.low_1pct_fps < stats.avg_fps


def test_spikes_counted():
    # median ~4ms, spikes are > 8ms
    frames = [4.0] * 50 + [20.0, 30.0]
    stats = stats_from_frame_times(frames)
    assert stats.spikes == 2


def test_parse_presentmon_csv_old_column():
    csv = "Application,msBetweenPresents\nFN,8.0\nFN,8.0\nFN,16.0\n"
    stats = parse_presentmon_csv(csv)
    assert stats.frames == 3


def test_parse_presentmon_csv_no_known_column():
    csv = "Application,SomethingElse\nFN,1\n"
    stats = parse_presentmon_csv(csv)
    assert stats.frames == 0


def _cmp(before, after):
    return Comparison("t", before, after)


BASE = FrameStats(1000, 240, 150, 120, 10, 60)


def test_improvement_keeps_better_1pct_low():
    better = FrameStats(1000, 242, 170, 130, 8, 60)  # +13% 1% low
    assert is_improvement(_cmp(BASE, better))


def test_improvement_rejects_noise():
    noise = FrameStats(1000, 241, 151, 121, 10, 60)
    assert not is_improvement(_cmp(BASE, noise))


def test_improvement_rejects_avg_regression_even_if_low_improves():
    worse = FrameStats(1000, 210, 170, 120, 8, 60)  # avg -12.5%
    assert not is_improvement(_cmp(BASE, worse))


def test_improvement_keeps_fewer_spikes_when_avg_holds():
    fewer = FrameStats(1000, 239, 151, 121, 3, 60)  # spikes 10->3, avg ~flat
    assert is_improvement(_cmp(BASE, fewer))


def test_improvement_keeps_big_avg_gain():
    faster = FrameStats(1000, 270, 150, 120, 10, 60)  # avg +12.5%
    assert is_improvement(_cmp(BASE, faster))


def test_abstate_round_trip(tmp_path):
    p = tmp_path / "ab.json"
    st = ABState(pending=["a", "b"], index=1, phase="applied",
                 baseline={"avg_fps": 240}, results={"a": True}, path=str(p))
    st.save()
    loaded = ABState.load(p)
    assert loaded.pending == ["a", "b"]
    assert loaded.index == 1
    assert loaded.current_tweak_id == "b"
    assert loaded.results == {"a": True}


def test_abstate_advance():
    st = ABState(pending=["a", "b"], index=0, phase="applied", baseline={})
    st.advance()
    assert st.index == 1 and st.phase == "baseline" and st.baseline is None


def test_abstate_load_missing_returns_none(tmp_path):
    assert ABState.load(tmp_path / "nope.json") is None
