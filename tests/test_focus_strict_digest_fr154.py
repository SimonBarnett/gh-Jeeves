"""FR #154: digest exposes focus_strict boolean without changing focus list shape."""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import public_digest_snapshot
from jeeves.focus import (
    focus_public_list,
    handle_focus_cmd,
    is_strict_focus,
    load_focus,
    set_focus,
    set_strict_focus,
)


def test_digest_focus_strict_on_off(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_focus(home, "o/r", priority=1, label="high")

    set_strict_focus(home, True)
    snap_on = public_digest_snapshot(home)
    assert snap_on.get("focus_strict") is True
    assert isinstance(snap_on.get("focus"), list)
    assert snap_on["focus"][0]["repo"] == "o/r"
    assert snap_on["focus"][0]["priority"] == 1
    assert "kind" in snap_on["focus"][0]

    set_strict_focus(home, False)
    snap_off = public_digest_snapshot(home)
    assert snap_off.get("focus_strict") is False
    assert isinstance(snap_off.get("focus"), list)
    assert snap_off["focus"][0]["repo"] == "o/r"


def test_digest_focus_strict_via_focus_cmd(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    assert handle_focus_cmd(home, "strict on") == ["focus strict: on"]
    snap = public_digest_snapshot(home)
    assert snap["focus_strict"] is True
    assert snap["focus"] == []

    assert handle_focus_cmd(home, "strict off") == ["focus strict: off"]
    snap2 = public_digest_snapshot(home)
    assert snap2["focus_strict"] is False


def test_digest_focus_strict_survives_reload(tmp_path: Path):
    """Value comes from focus.json — restart/resync = reload file."""
    home = tmp_path / "d"
    home.mkdir()
    set_strict_focus(home, True)
    assert load_focus(home).get("strict") is True
    assert is_strict_focus(home) is True
    # Fresh snapshot (as after process restart reading same home)
    snap = public_digest_snapshot(home)
    assert snap["focus_strict"] is True
    # List shape unchanged when empty
    assert focus_public_list(home) == []
    assert snap["focus"] == []


def test_digest_default_focus_strict_false(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    snap = public_digest_snapshot(home)
    assert snap.get("focus_strict") is False
    assert isinstance(snap.get("focus"), list)
