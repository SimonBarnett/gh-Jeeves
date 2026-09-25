"""
G1: token-less end-to-end gate (CI).

Local test ircd + stub receiver + scripted fake worker.
No LLM; no live IRC. Process guard blocks non-loopback HTTP.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pytest

from jeeves.guard import no_llm_network_guard, NonLoopbackBlocked
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.nicks import canonical_worker_nick, parse_worker_nick
from jeeves.queue import load_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


@pytest.fixture
def g1_home(tmp_path: Path) -> Path:
    home = tmp_path / "digest"
    home.mkdir()
    return home


def _post_git(base: str, event: str, payload: dict) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/bob/v1/git",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def _get_report(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/bob/v1/report", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_worker_nick_grammar():
    assert parse_worker_nick("flamingo-46804") == ("flamingo", "46804")
    assert canonical_worker_nick("flamingo-46804") == "flamingo-46804"
    assert canonical_worker_nick("w-fla-99") == "fla-99"
    assert parse_worker_nick("Jeeves") is None


def test_g1_guard_blocks_non_loopback():
    with no_llm_network_guard():
        with pytest.raises(NonLoopbackBlocked):
            import socket

            socket.create_connection(("1.1.1.1", 80), timeout=1.0)


def test_g1_token_less_e2e_chain(g1_home: Path):
    """
    Full chain (FR #106):
    1 GitHub POST → announce + queue
    2 worker !bored
    3 Jeeves assign line
    4 worker ACK → accepted + busy
    5 worker DONE → done + idle
    """
    with no_llm_network_guard():
        ircd = LocalIrcd()
        port = ircd.start()
        rx = StubReceiver(g1_home)
        rport = rx.start()
        base = f"http://127.0.0.1:{rport}"

        jeeves = JeevesChair("127.0.0.1", port, g1_home, base, shops=["#flamingo"])
        jeeves.live_seats_override = {"flamingo-9001"}
        ear = BobEar("127.0.0.1", port, g1_home, machine="flamingo")
        worker = IrcClient("127.0.0.1", port, "flamingo-9001")
        worker.join("#flamingo")

        jeeves.start()
        ear.start()
        time.sleep(0.2)

        try:
            # --- step 1: GitHub issues opened ---
            payload = {
                "action": "opened",
                "issue": {
                    "number": 1,
                    "title": "FR token-less gate mentions ghp_FAKE_not_a_real_secret in body only",
                    "body": "See ghp_should_not_reject_whole_payload",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/issues/1",
                },
                "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
                "sender": {"login": "tester"},
            }
            status = _post_git(base, "issues", payload)
            assert status == 204

            # wait for Jeeves to drain outbox → #bobiverse (FR #24 vital-first line)
            deadline = time.time() + 5
            while time.time() < deadline and not any(
                x.startswith("announce:GIT ") and "SimonBarnett/gh-Jeeves" in x for x in jeeves.handled
            ):
                time.sleep(0.05)
            ann = next(x for x in jeeves.handled if x.startswith("announce:GIT "))
            assert "SimonBarnett/gh-Jeeves#1" in ann or "SimonBarnett/gh-Jeeves opened #1" in ann
            assert "FR" in ann or "issues" in ann
            assert "opened" in ann

            snap = _get_report(base)
            unacc = snap["queue"]["unaccepted"]
            assert len(unacc) == 1
            assert unacc[0]["task"] == "FR"
            assert unacc[0]["id"] == "#1"
            assert unacc[0]["repo"] == "SimonBarnett/gh-Jeeves"

            # --- steps 2–3: !bored → Jeeves assign ---
            worker.privmsg("#flamingo", "!bored")
            assign = worker.wait_privmsg(
                predicate=lambda m: m[0].lower() == "jeeves"
                and m[2].startswith("flamingo-9001:")
                and "OFFER" not in m[2],
                timeout=5.0,
            )
            assert assign is not None
            assert "FR SimonBarnett/gh-Jeeves#1" in assign[2]
            assert any(h.startswith("assign:flamingo-9001:") for h in jeeves.handled)
            assert any(str(x).startswith("retired_bored:") for x in ear.offers)

            # --- step 4: ACK ---
            worker.privmsg("#flamingo", "ACK FR SimonBarnett/gh-Jeeves#1")
            deadline = time.time() + 5
            while time.time() < deadline and not any(h.startswith("ack:") for h in jeeves.handled):
                time.sleep(0.05)
            assert any("ack:flamingo-9001:SimonBarnett/gh-Jeeves#1" in h for h in jeeves.handled)

            q = load_queue(g1_home)
            assert q["unaccepted"] == []
            assert len(q["accepted"]) == 1
            assert q["accepted"][0]["nick"] == "flamingo-9001"
            assert q["workers"]["flamingo-9001"]["state"] == "busy"

            snap = _get_report(base)
            assert snap["workers"]["flamingo-9001"]["state"] == "busy"

            # --- step 5: DONE (AI work skipped — scripts only) ---
            worker.privmsg(
                "#flamingo",
                "DONE FR SimonBarnett/gh-Jeeves#1 ok https://example.local/pr/1",
            )
            deadline = time.time() + 5
            while time.time() < deadline and not any(h.startswith("done:") for h in jeeves.handled):
                time.sleep(0.05)
            assert any("done:flamingo-9001:" in h for h in jeeves.handled)

            q = load_queue(g1_home)
            assert q["accepted"] == []
            assert len(q["done"]) == 1
            assert q["workers"]["flamingo-9001"]["state"] == "idle"

            snap = _get_report(base)
            assert snap["workers"]["flamingo-9001"]["state"] == "idle"

            # supersede path: PR opened referencing #1 → MRB
            pr_payload = {
                "action": "opened",
                "pull_request": {
                    "number": 2,
                    "title": "Implement G1 closes #1",
                    "body": "closes #1",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/pull/2",
                    "merged": False,
                },
                "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
                "sender": {"login": "tester"},
            }
            assert _post_git(base, "pull_request", pr_payload) == 204
            time.sleep(0.3)
            q = load_queue(g1_home)
            tasks = {(r["task"], r["id"]) for r in q["unaccepted"]}
            assert ("MRB", "#2") in tasks

        finally:
            jeeves.stop()
            ear.stop()
            worker.close()
            rx.stop()
            ircd.stop()


def test_secret_filter_title_body_ok_secret_field_rejected(g1_home: Path):
    with no_llm_network_guard():
        rx = StubReceiver(g1_home)
        rport = rx.start()
        base = f"http://127.0.0.1:{rport}"
        try:
            # title mentions marker — must accept
            ok = {
                "action": "opened",
                "issue": {
                    "number": 9,
                    "title": "docs mention ghp_example in prose",
                    "body": "no real secret",
                    "html_url": "https://github.com/o/r/issues/9",
                },
                "repository": {"full_name": "o/r"},
            }
            assert _post_git(base, "issues", ok) == 204

            # secret field contains marker — must 400
            bad = {
                "action": "opened",
                "issue": {"number": 10, "title": "x", "body": "y"},
                "repository": {"full_name": "o/r"},
                "token": "ghp_REALLOOKING",
            }
            try:
                _post_git(base, "issues", bad)
                raised = False
            except Exception as e:
                raised = True
                assert "400" in str(e) or hasattr(e, "code")
            assert raised
        finally:
            rx.stop()
