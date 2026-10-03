"""FR #226: after GIVEUP/NACK, do not re-offer the same row to the same nick."""
from __future__ import annotations

import time
from pathlib import Path

from jeeves.assign import ChairAssignState, nick_on_giveup_cooldown
from jeeves.queue import (
    GIVEUP_COOLDOWN_S,
    GIVEUP_NEEDS_HUMAN_COUNT,
    accept_job,
    load_queue,
    nack_job,
    save_queue,
)


REPO = "SimonBarnett/gh-Jeeves"
AUTHOR = "marchhare-41912"
OTHER = "flamingo-1"


def _seed_accepted(home: Path, nick: str = AUTHOR) -> None:
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#225",
                    "url": f"https://github.com/{REPO}/pull/225",
                    "line": "MRB #225",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st, _ = accept_job(home, nick, "#marchhare", "MRB", REPO, "225")
    assert st == "accepted"


def test_nack_stamps_per_nick_cooldown(tmp_path: Path):
    home = tmp_path / "g1"
    home.mkdir()
    _seed_accepted(home)
    st, row = nack_job(home, AUTHOR, "MRB", REPO, "225")
    assert st == "nacked"
    assert row is not None
    assert int(row.get("giveup_count") or 0) == 1
    giveups = row.get("giveup_by") or {}
    entry = giveups.get(AUTHOR.lower())
    assert isinstance(entry, dict)
    assert int(entry.get("count") or 0) == 1
    assert entry.get("cooldown_until")
    assert row.get("cooldown_until") == entry.get("cooldown_until")
    assert not row.get("needs_human")


def test_second_giveup_sets_needs_human(tmp_path: Path):
    home = tmp_path / "g2"
    home.mkdir()
    _seed_accepted(home)
    nack_job(home, AUTHOR, "MRB", REPO, "225")
    # re-accept then give up again
    q = load_queue(home)
    assert len(q.get("unaccepted") or []) == 1
    st, _ = accept_job(home, AUTHOR, "#marchhare", "MRB", REPO, "225")
    assert st == "accepted"
    st2, row = nack_job(home, AUTHOR, "MRB", REPO, "225")
    assert st2 == "nacked"
    assert int(row.get("giveup_count") or 0) >= GIVEUP_NEEDS_HUMAN_COUNT
    assert row.get("needs_human") in (True, "true", "1", 1)
    giveups = row.get("giveup_by") or {}
    assert int((giveups.get(AUTHOR.lower()) or {}).get("count") or 0) >= 2


def test_assign_skips_giveup_nick_during_cooldown(tmp_path: Path):
    home = tmp_path / "g3"
    home.mkdir()
    _seed_accepted(home)
    nack_job(home, AUTHOR, "MRB", REPO, "225")
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(
        home,
        AUTHOR,
        "#marchhare",
        live_nicks={AUTHOR, OTHER},
    )
    assert d.action == "nothing"
    assert "nothing queued" in (d.line or "")


def test_other_seat_gets_row_during_author_cooldown(tmp_path: Path):
    home = tmp_path / "g4"
    home.mkdir()
    _seed_accepted(home)
    nack_job(home, AUTHOR, "MRB", REPO, "225")
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(
        home,
        OTHER,
        "#flamingo",
        live_nicks={AUTHOR, OTHER},
    )
    assert d.action == "assign"
    assert "MRB" in (d.line or "")
    assert "#225" in (d.line or "")


def test_needs_human_blocks_all_seats(tmp_path: Path):
    home = tmp_path / "g5"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#225",
                    "url": f"https://github.com/{REPO}/pull/225",
                    "needs_human": True,
                    "giveup_count": 2,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(
        home,
        OTHER,
        "#flamingo",
        live_nicks={AUTHOR, OTHER},
    )
    assert d.action == "nothing"


def test_nick_on_giveup_cooldown_helpers():
    now = time.time()
    future = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + float(GIVEUP_COOLDOWN_S))
    )
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 10))
    row = {
        "giveup_by": {
            AUTHOR.lower(): {"count": 1, "cooldown_until": future},
        },
        "cooldown_until": future,
    }
    assert nick_on_giveup_cooldown(row, AUTHOR, now)
    assert not nick_on_giveup_cooldown(row, OTHER, now)
    row_expired = {
        "giveup_by": {
            AUTHOR.lower(): {"count": 1, "cooldown_until": past},
        }
    }
    assert not nick_on_giveup_cooldown(row_expired, AUTHOR, now)
    # legacy row-wide only when giveup_by absent
    assert nick_on_giveup_cooldown({"cooldown_until": future}, OTHER, now)
    assert not nick_on_giveup_cooldown({"cooldown_until": past}, OTHER, now)
