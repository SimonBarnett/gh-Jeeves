"""agentic_build #387: digest merge must keep machines.<id>.weekly + period_end."""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import apply_report, coerce_machine, load_digest, public_digest_snapshot


def test_coerce_machine_keeps_weekly_and_period_end():
    m = coerce_machine(
        "marchhare",
        {
            "online": True,
            "weekly": 63,
            "period_end": "2026-09-29T23:41:45+00:00",
            "pcent": {},
        },
    )
    assert m["weekly"] == 63
    assert "2026-09-29" in m["period_end"]


def test_merge_op_persists_weekly_on_get(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(
        home,
        {
            "op": "merge",
            "machine": "marchhare",
            "online": True,
            "status": "operational",
            "weekly": 63,
            "period_end": "2026-09-29T23:41:45.639212+00:00",
            "working_on": "FR ear",
            "pcent": {},
        },
    )
    assert out.ok
    doc = load_digest(home)
    mh = doc["machines"]["marchhare"]
    assert mh["weekly"] == 63
    assert "2026-09-29" in mh["period_end"]
    snap = public_digest_snapshot(home)
    assert snap["machines"]["marchhare"]["weekly"] == 63
    assert snap["machines"]["marchhare"].get("period_end")


def test_empty_pcent_does_not_wipe_existing(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home,
        {
            "op": "merge",
            "machine": "marchhare",
            "online": True,
            "weekly": 63,
            "period_end": "2026-09-29T00:00:00+00:00",
            "pcent": {"grok-chat": 3},
        },
    )
    apply_report(
        home,
        {
            "op": "merge",
            "machine": "marchhare",
            "online": True,
            "weekly": 63,
            "period_end": "2026-09-29T00:00:00+00:00",
            "pcent": {},
        },
    )
    mh = load_digest(home)["machines"]["marchhare"]
    assert mh["weekly"] == 63
    assert mh["pcent"].get("grok-chat") == 3
