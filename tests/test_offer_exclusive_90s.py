"""CAST IRON: one open offer per row_key; 90s before re-offer to another seat."""

from __future__ import annotations

import json
import time
from pathlib import Path

from jeeves.assign import (
    DEFAULT_OFFER_TIMEOUT_S,
    ChairAssignState,
    offer_timeout_s_from_env,
    row_key,
)


def _home(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    home = tmp_path / "bobiverse"
    home.mkdir()
    if rows is None:
        rows = [
            {
                "task": "MRB",
                "repo": "SimonBarnett/gh-Jeeves",
                "id": "#193",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/pull/193",
                "seq": 1,
                "line": "exclusive offer fixture",
            }
        ]
    (home / "queue.json").write_text(
        json.dumps(
            {
                "v": 1,
                "unaccepted": rows,
                "accepted": [],
                "done": [],
                "workers": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (home / "focus.json").write_text(
        json.dumps({"v": 1, "strict": False, "repos": {}, "items": {}}) + "\n",
        encoding="utf-8",
    )
    (home / "ignored.json").write_text(
        json.dumps({"v": 1, "repos": [], "items": []}) + "\n", encoding="utf-8"
    )
    return home


def test_default_offer_timeout_is_90s():
    assert DEFAULT_OFFER_TIMEOUT_S == 90.0


def test_offer_timeout_env_override(monkeypatch):
    monkeypatch.setenv("JEEVES_OFFER_TIMEOUT_S", "45")
    assert offer_timeout_s_from_env() == 45.0
    monkeypatch.setenv("JEEVES_OFFER_TIMEOUT_S", "bad")
    assert offer_timeout_s_from_env(12.0) == 12.0


def test_second_worker_cannot_take_same_row_key_while_offer_open(tmp_path: Path):
    home = _home(tmp_path)
    st = ChairAssignState(timeout_s=90.0)
    st.bind(home)
    now = time.time()
    d1 = st.decide(
        home,
        "marchhare-14764",
        "#marchhare",
        live_nicks={"marchhare-14764", "ionos-14020"},
        now=now,
    )
    assert d1.action == "assign"
    assert d1.line and "gh-Jeeves#193" in d1.line
    assert row_key(d1.row) in st.offered_keys()

    d2 = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"marchhare-14764", "ionos-14020"},
        now=now + 10,
    )
    # Same work still exclusively held — other seat gets nothing queued.
    assert d2.action == "nothing"
    assert d2.reason == "none_eligible"
    assert d2.line and "nothing queued" in d2.line


def test_same_work_reoffer_only_after_90s_timeout(tmp_path: Path):
    home = _home(tmp_path)
    st = ChairAssignState(timeout_s=90.0)
    st.bind(home)
    now = time.time()
    d1 = st.decide(
        home, "marchhare-14764", "#marchhare", live_nicks={"marchhare-14764"}, now=now
    )
    assert d1.action == "assign"

    # Still inside the 90s window — other seat blocked.
    d_mid = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"marchhare-14764", "ionos-14020"},
        now=now + 89,
    )
    assert d_mid.action == "nothing"

    # After timeout, open offer expires and the job may be offered again.
    d3 = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"marchhare-14764", "ionos-14020"},
        now=now + 90,
    )
    assert d3.action == "assign"
    assert d3.line and "ionos-14020:" in d3.line and "gh-Jeeves#193" in d3.line


def test_ack_no_match_must_not_clear_open_offer(tmp_path: Path):
    """Regression: clearing on ACK no_match dual-offered MRB #193 to two seats."""
    home = _home(tmp_path)
    st = ChairAssignState(timeout_s=90.0)
    st.bind(home)
    now = time.time()
    d1 = st.decide(
        home, "marchhare-14764", "#marchhare", live_nicks={"marchhare-14764"}, now=now
    )
    assert d1.action == "assign"
    # Simulate roles.py: only on_ack when accept matched — no_match leaves open.
    assert st.has_open("marchhare-14764")
    d2 = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"marchhare-14764", "ionos-14020"},
        now=now + 5,
    )
    assert d2.action == "nothing"
    assert st.has_open("marchhare-14764")


def test_successful_ack_releases_exclusive_lock(tmp_path: Path):
    home = _home(
        tmp_path,
        rows=[
            {
                "task": "MRB",
                "repo": "SimonBarnett/gh-Jeeves",
                "id": "#193",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/pull/193",
                "seq": 1,
                "line": "a",
            },
            {
                "task": "FR",
                "repo": "SimonBarnett/gh-Jeeves",
                "id": "#200",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/200",
                "seq": 2,
                "line": "b",
            },
        ],
    )
    st = ChairAssignState(timeout_s=90.0)
    st.bind(home)
    now = time.time()
    d1 = st.decide(
        home, "marchhare-14764", "#marchhare", live_nicks={"marchhare-14764"}, now=now
    )
    assert d1.action == "assign"
    st.on_ack("marchhare-14764")
    assert not st.has_open("marchhare-14764")
    # Next seat can take the next unaccepted job (not the accepted one's lock).
    d2 = st.decide(
        home, "ionos-14020", "#ionos", live_nicks={"ionos-14020"}, now=now + 1
    )
    assert d2.action == "assign"
    assert "gh-Jeeves#193" in (d2.line or "") or "gh-Jeeves#200" in (d2.line or "")
