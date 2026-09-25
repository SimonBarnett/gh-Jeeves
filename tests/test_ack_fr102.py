"""FR #102: ACK FR matches legacy PR queue rows; busy on no-match; trailing text; stale nicks."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from jeeves.queue import (
    _ack_no_match_logged,
    accept_job,
    expire_stale_workers,
    load_queue,
    release_worker,
    save_queue,
    worker_state,
)
from jeeves.roles import JeevesChair
from jeeves.wire import ack_format_hint, looks_like_ack, parse_ack


def test_parse_ack_exact_and_url_suffix():
    exact = parse_ack("ACK FR SimonBarnett/gh-Jeeves#15")
    assert exact is not None
    assert exact.task == "FR"
    assert exact.number == "15"
    assert exact.extra == ""

    with_url = parse_ack(
        "ACK FR SimonBarnett/gh-Jeeves#15 https://github.com/SimonBarnett/gh-Jeeves/issues/15"
    )
    assert with_url is not None
    assert with_url.number == "15"
    assert "https://github.com/" in with_url.extra


def test_looks_like_ack_bad_format_gets_hint():
    assert looks_like_ack("ACK PR SimonBarnett/gh-Jeeves#15")
    assert parse_ack("ACK PR SimonBarnett/gh-Jeeves#15") is None
    assert "ACK FR|MRB|UAT" in ack_format_hint()


def test_ack_fr_matches_legacy_pr_queue_row(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    doc = {
        "v": 1,
        "unaccepted": [
            {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "PR",
                "id": "#15",
                "line": "K14",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/15",
            }
        ],
        "accepted": [],
        "done": [],
        "workers": {},
    }
    save_queue(home, doc)
    # load migrates PR → FR
    loaded = load_queue(home)
    assert loaded["unaccepted"][0]["task"] == "FR"

    st, row = accept_job(
        home, "marchhare-31712", "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "15"
    )
    assert st == "accepted"
    assert row is not None
    assert worker_state(home, "marchhare-31712") == "busy"
    q = load_queue(home)
    assert q["accepted"][0]["task"] == "FR"
    assert q["accepted"][0]["nick"] == "marchhare-31712"


def test_ack_fr_matches_pr_without_relying_only_on_migrate(tmp_path: Path):
    """Equivalence even if a PR row somehow remains."""
    home = tmp_path / "d2"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "PR",
                    "id": "#15",
                    "line": "x",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    # Bypass migrate by accepting against in-memory path: re-write PR after load
    doc = load_queue(home)
    doc["unaccepted"][0]["task"] = "PR"
    path = home / "queue.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    # accept_job calls load_queue which migrates again — still OK
    st, _ = accept_job(home, "marchhare-1", "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "15")
    assert st == "accepted"


def test_no_match_ack_records_busy_and_logs_once(tmp_path: Path, caplog):
    home = tmp_path / "d3"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    _ack_no_match_logged.clear()
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a, **k):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    chair = JeevesChair(
        "127.0.0.1", 1, home, "http://127.0.0.1:9", shops=["#marchhare"], client=FakeClient()
    )
    chair._post_report = lambda payload: posts.append(payload)  # type: ignore
    with caplog.at_level(logging.WARNING, logger="jeeves.chair"):
        chair._handle_shop(
            "marchhare-31712", "#marchhare", "ACK FR SimonBarnett/gh-Jeeves#999"
        )
        chair._handle_shop(
            "marchhare-31712", "#marchhare", "ACK FR SimonBarnett/gh-Jeeves#999"
        )
    assert worker_state(home, "marchhare-31712") == "busy"
    assert any(h.startswith("ack_no_match:marchhare-31712:") for h in chair.handled)
    assert any(p.get("op") == "worker_state" and p.get("state") == "busy" for p in posts)
    warns = [r for r in caplog.records if "ack_no_match" in r.getMessage()]
    assert len(warns) == 1


def test_trailing_url_ack_accepted(tmp_path: Path):
    home = tmp_path / "d4"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#15",
                    "line": "x",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a, **k):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    chair = JeevesChair(
        "127.0.0.1", 1, home, "http://127.0.0.1:9", shops=["#marchhare"], client=FakeClient()
    )
    chair._post_report = lambda payload: posts.append(payload)  # type: ignore
    chair._handle_shop(
        "marchhare-31712",
        "#marchhare",
        "ACK FR SimonBarnett/gh-Jeeves#15 https://github.com/SimonBarnett/gh-Jeeves/issues/15",
    )
    assert any(h.startswith("ack:marchhare-31712:") for h in chair.handled)
    assert load_queue(home)["accepted"]


def test_bad_ack_gets_pm_hint(tmp_path: Path):
    home = tmp_path / "d5"
    home.mkdir()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a, **k):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    chair = JeevesChair(
        "127.0.0.1", 1, home, "http://127.0.0.1:9", shops=["#marchhare"], client=FakeClient()
    )
    chair._post_report = lambda payload: None  # type: ignore
    chair._handle_shop("marchhare-31712", "#marchhare", "ACK PR SimonBarnett/gh-Jeeves#15")
    assert any(h.startswith("ack_reject:") for h in chair.handled)
    assert chair.pm_egress
    assert "ACK FR|MRB|UAT" in chair.pm_egress[0][1]


def test_stale_nick_released_and_same_machine_reack(tmp_path: Path):
    home = tmp_path / "d6"
    home.mkdir()
    old = "marchhare-42356"
    new = "marchhare-31712"
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#70",
                    "nick": old,
                    "channel": "#marchhare",
                    "line": "token-less",
                }
            ],
            "done": [],
            "workers": {
                old: {
                    "state": "busy",
                    "job": "SimonBarnett/gh-Jeeves FR #70",
                    "ts": "2020-01-01T00:00:00Z",
                }
            },
        },
    )
    expired = expire_stale_workers(home, idle_s=60.0, now=time.time())
    assert old in expired
    q = load_queue(home)
    assert old not in (q.get("workers") or {})
    assert any(r.get("id") == "#70" for r in q.get("unaccepted") or [])

    # Re-seed accepted held by old nick (restart race before expire)
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#70",
                    "nick": old,
                    "channel": "#marchhare",
                }
            ],
            "done": [],
            "workers": {old: {"state": "busy", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}},
        },
    )
    st, row = accept_job(home, new, "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "70")
    assert st == "accepted"
    assert row is not None
    assert row.get("nick") == new
    q2 = load_queue(home)
    assert old not in (q2.get("workers") or {})
    assert worker_state(home, new) == "busy"


def test_quit_releases_worker(tmp_path: Path):
    home = tmp_path / "d7"
    home.mkdir()
    nick = "marchhare-42356"
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#70",
                    "nick": nick,
                }
            ],
            "done": [],
            "workers": {nick: {"state": "busy", "ts": "2026-09-25T18:00:00Z"}},
        },
    )
    released = release_worker(home, nick, reason="quit")
    assert len(released) == 1
    q = load_queue(home)
    assert nick not in q["workers"]
    assert q["unaccepted"][0]["id"] == "#70"
