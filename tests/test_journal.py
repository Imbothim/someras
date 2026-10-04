"""Journal encode/decode and persistence."""

from __future__ import annotations

from pathlib import Path

from tweaker.backend import MISSING, RegValue
from tweaker.journal import Journal, JournalEntry, _decode, _encode, new_journal


def test_encode_decode_regvalue_dword():
    rv = RegValue(42, "REG_DWORD")
    assert _decode(_encode(rv)) == rv


def test_encode_decode_regvalue_binary():
    rv = RegValue(b"\x01\x02\xff", "REG_BINARY")
    out = _decode(_encode(rv))
    assert out.data == b"\x01\x02\xff" and out.type == "REG_BINARY"


def test_encode_decode_missing_sentinel():
    assert _decode(_encode(MISSING)) is MISSING


def test_encode_decode_nested_dict_with_regvalue():
    payload = {"hive": "HKLM", "subkey": "X", "name": "V",
               "value": RegValue(5, "REG_DWORD")}
    out = _decode(_encode(payload))
    assert out["value"] == RegValue(5, "REG_DWORD")
    assert out["hive"] == "HKLM"


def test_encode_decode_nested_missing_value():
    payload = {"value": MISSING, "name": "x"}
    out = _decode(_encode(payload))
    assert out["value"] is MISSING


def test_journal_save_and_load(tmp_path: Path):
    j = Journal(path=tmp_path / "j.json")
    j.record(JournalEntry("reg", "HKLM\\X\\V",
                          {"hive": "HKLM", "subkey": "X", "name": "V",
                           "value": RegValue(1, "REG_DWORD")},
                          tweak_id="t1"))
    j.record(JournalEntry("command", "foo",
                          {"label": "foo", "revert_args": ["foo", "off"]}))
    j.save()
    loaded = Journal.load(tmp_path / "j.json")
    assert len(loaded.entries) == 2
    assert loaded.entries[0].before["value"] == RegValue(1, "REG_DWORD")
    assert loaded.entries[1].before["revert_args"] == ["foo", "off"]


def test_new_journal_uses_directory(tmp_path: Path):
    j = new_journal(tmp_path)
    assert j.path.parent == tmp_path
    assert j.path.name.startswith("journal-")
