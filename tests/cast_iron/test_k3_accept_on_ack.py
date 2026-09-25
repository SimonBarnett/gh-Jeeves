"""K3 / FR #4: nothing marked accepted after ACK — gate-blocking.

Evidence (brief): ionos queue.json had 28 unaccepted, 0 accepted despite many ACKs.
Outcome: accept on ACK; busy/idle on the webhook (agentic_irc #211).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jeeves.queue import (
    Claim,
    accept_job,
    accepted_rows,
    apply_queue_event,
    complete_job,
    load_queue,
    nack_job,
    queue_counts,
    worker_state,
)
from jeeves.wire import parse_ack, parse_done, parse_nack


def _enqueue(home: Path, n: int = 3, repo: str = "SimonBarnett/gh-Jeeves") -> None:
    for i in range(1, n + 1):
        apply_queue_event(
            home,
            Claim(
                repo=repo,
                task="FR",
                id=f"#{i}",
                event="issues",
                action="opened",
                line=f"title {i}",
                url=f"https://github.com/{repo}/issues/{i}",
            ),
        )


def test_k3_fail_if_ack_leaves_accepted_empty(tmp_path: Path):
    """The seed failing condition: after ACK, accepted must not stay empty."""
    home = tmp_path / "digest"
    home.mkdir()
    _enqueue(home, 5)
    before = queue_counts(home)
    assert before["unaccepted"] == 5
    assert before["accepted"] == 0

    st, row = accept_job(home, "flamingo-9001", "#flamingo", "FR", "SimonBarnett/gh-Jeeves", "1")
    assert st == "accepted"
    assert row is not None

    after = queue_counts(home)
    # K3: this is the gate assertion that was failing on ionos (0 accepted).
    assert after["accepted"] >= 1, "K3 FAIL: accepted stayed empty after ACK"
    assert after["unaccepted"] == before["unaccepted"] - 1
    assert worker_state(home, "flamingo-9001") == "busy"
    rows = accepted_rows(home)
    assert any(r.get("nick") == "flamingo-9001" and r.get("id") == "#1" for r in rows)


def test_k3_many_acks_drain_unaccepted(tmp_path: Path):
    """28-unaccepted style: N ACKs → N accepted, 0 stuck in unaccepted for those ids."""
    home = tmp_path / "d2"
    home.mkdir()
    n = 28
    _enqueue(home, n)
    assert queue_counts(home)["accepted"] == 0
    for i in range(1, n + 1):
        nick = f"flamingo-{9000 + i}"
        st, _ = accept_job(home, nick, "#flamingo", "FR", "SimonBarnett/gh-Jeeves", str(i))
        assert st == "accepted"
    counts = queue_counts(home)
    assert counts["unaccepted"] == 0
    assert counts["accepted"] == n
    assert len(accepted_rows(home)) == n


def test_k3_ack_idempotent_same_nick(tmp_path: Path):
    home = tmp_path / "d3"
    home.mkdir()
    _enqueue(home, 1)
    st1, r1 = accept_job(home, "mh-1", "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "#1")
    st2, r2 = accept_job(home, "mh-1", "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "1")
    assert st1 == st2 == "accepted"
    assert queue_counts(home)["accepted"] == 1
    assert queue_counts(home)["unaccepted"] == 0


def test_k3_ack_no_match_when_missing(tmp_path: Path):
    home = tmp_path / "d4"
    home.mkdir()
    _enqueue(home, 1)
    st, row = accept_job(home, "mh-1", "#m", "FR", "SimonBarnett/gh-Jeeves", "99")
    assert st == "no_match"
    assert row is None
    assert queue_counts(home)["accepted"] == 0
    # FR #102: parseable ACK still records the worker busy on no_match
    assert worker_state(home, "mh-1") == "busy"


def test_k3_done_and_nack(tmp_path: Path):
    home = tmp_path / "d5"
    home.mkdir()
    _enqueue(home, 2)
    accept_job(home, "w-1", "#flamingo", "FR", "SimonBarnett/gh-Jeeves", "1")
    st, _ = complete_job(home, "w-1", "FR", "SimonBarnett/gh-Jeeves", "1", "PASS", "https://x")
    assert st == "done"
    assert worker_state(home, "w-1") == "idle"
    assert queue_counts(home)["accepted"] == 0
    assert queue_counts(home)["done"] == 1

    accept_job(home, "w-2", "#flamingo", "FR", "SimonBarnett/gh-Jeeves", "2")
    st2, _ = nack_job(home, "w-2", "FR", "SimonBarnett/gh-Jeeves", "2")
    assert st2 == "nacked"
    assert worker_state(home, "w-2") == "idle"
    assert queue_counts(home)["unaccepted"] == 1
    assert queue_counts(home)["accepted"] == 0


def test_parse_ack_flexible_hash():
    a = parse_ack("ACK FR SimonBarnett/gh-Jeeves#4")
    assert a is not None
    assert a.task == "FR"
    assert a.repo == "SimonBarnett/gh-Jeeves"
    assert a.number == "4"
    b = parse_ack("ACK FR SimonBarnett/gh-Jeeves #4")
    assert b is not None and b.number == "4"
    assert parse_nack("NACK FR SimonBarnett/gh-Jeeves#4") is not None
    assert parse_done("DONE FR SimonBarnett/gh-Jeeves#4 PASS https://x") is not None


def test_k3_chair_ack_posts_queue_accept(tmp_path: Path, monkeypatch):
    """Chair silent path: ACK → accept_job + queue_accept report (busy)."""
    from jeeves.roles import JeevesChair

    home = tmp_path / "chair"
    home.mkdir()
    _enqueue(home, 1)
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    monkeypatch.setattr("jeeves.roles.IrcClient", FakeClient)
    chair = JeevesChair("127.0.0.1", 1, home, "http://127.0.0.1:9", shops=["#flamingo"])
    chair._post_report = lambda payload: posts.append(payload)  # type: ignore
    chair._handle_shop("flamingo-9001", "#flamingo", "ACK FR SimonBarnett/gh-Jeeves#1")
    assert any(h.startswith("ack:flamingo-9001:") for h in chair.handled)
    assert queue_counts(home)["accepted"] == 1
    assert any(p.get("op") == "queue_accept" for p in posts)
    assert any(p.get("op") == "worker_state" and p.get("state") == "busy" for p in posts)
    assert worker_state(home, "flamingo-9001") == "busy"
