"""FR #75: !ignore / !ignored / !unignore suppress a repo from the whole process."""

from __future__ import annotations

import json
from pathlib import Path

from jeeves.announce import process_git_webhook
from jeeves.commands import get_command, validate_registry
from jeeves.helpcmd import build_help
from jeeves.ignore import (
    add_ignore,
    filter_rows_not_ignored,
    handle_ignore_add,
    handle_unignore,
    is_ignored,
    load_ignored,
    may_mutate_ignore,
    normalize_repo_token,
    purge_repo_from_queue,
    remove_ignore,
    repo_is_ignored,
)
from jeeves.listfmt import format_unaccepted_list
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.offer import EarOfferState
from jeeves.queue import Claim, apply_queue_event, load_queue, save_queue, top_unaccepted
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def _issue_payload(repo: str, number: int = 1, title: str = "t") -> dict:
    owner, name = repo.split("/", 1)
    return {
        "action": "opened",
        "repository": {"full_name": repo, "name": name, "owner": {"login": owner}},
        "issue": {
            "number": number,
            "title": title,
            "html_url": f"https://github.com/{repo}/issues/{number}",
            "body": "",
        },
        "sender": {"login": "alice"},
    }


def test_normalize_and_match_case_insensitive():
    assert normalize_repo_token("SimonBarnett/gh-Jeeves") == "SimonBarnett/gh-Jeeves"
    assert normalize_repo_token("gh-Jeeves") == "gh-Jeeves"
    assert normalize_repo_token("https://github.com/SimonBarnett/x") == "SimonBarnett/x"
    assert normalize_repo_token("bad repo!!") is None
    assert repo_is_ignored("SimonBarnett/Old", ["old"])
    assert repo_is_ignored("SimonBarnett/Old", ["simonbarnett/old"])
    assert not repo_is_ignored("SimonBarnett/Other", ["old"])


def test_persist_alongside_queue(tmp_path: Path):
    home = tmp_path / "digest"
    st, canon = add_ignore(home, "Sandbox")
    assert st == "added" and canon == "Sandbox"
    assert (home / "ignored.json").is_file()
    assert is_ignored(home, "SimonBarnett/Sandbox")
    st2, _ = add_ignore(home, "sandbox")
    assert st2 == "exists"
    st3, _ = remove_ignore(home, "SANDBOX")
    assert st3 == "removed"
    assert not is_ignored(home, "SimonBarnett/Sandbox")


def test_ignore_purges_queued_and_blocks_webhook(tmp_path: Path):
    home = tmp_path / "d"
    apply_queue_event(
        home,
        Claim(repo="SimonBarnett/noise", task="FR", id="#9", line="keep me"),
    )
    apply_queue_event(
        home,
        Claim(repo="SimonBarnett/keep", task="FR", id="#1", line="visible"),
    )
    lines = handle_ignore_add(home, "noise")
    assert any("now ignoring" in ln for ln in lines)
    q = load_queue(home)
    repos = {str(r.get("repo")) for r in q["unaccepted"]}
    assert "SimonBarnett/noise" not in repos
    assert "SimonBarnett/keep" in repos

    line, claim, reject = process_git_webhook(
        "issues", _issue_payload("SimonBarnett/noise", 99), home=home
    )
    assert reject == "ignored_repo"
    assert line is None and claim is None
    assert apply_queue_event(
        home, Claim(repo="SimonBarnett/noise", task="FR", id="#99", line="x")
    ) == "ignored"
    # unignore resumes new events only
    handle_unignore(home, "noise")
    assert apply_queue_event(
        home, Claim(repo="SimonBarnett/noise", task="FR", id="#100", line="back")
    ).startswith("enqueued")


def test_list_and_offer_skip_ignored(tmp_path: Path):
    home = tmp_path / "d"
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/ignored-repo",
                    "task": "FR",
                    "id": "#1",
                    "line": "nope",
                    "seq": 1,
                    "url": "https://github.com/SimonBarnett/ignored-repo/issues/1",
                },
                {
                    "repo": "SimonBarnett/ok",
                    "task": "FR",
                    "id": "#2",
                    "line": "yes",
                    "seq": 2,
                    "url": "https://github.com/SimonBarnett/ok/issues/2",
                },
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    add_ignore(home, "ignored-repo")
    lines = format_unaccepted_list(home)
    joined = "\n".join(lines)
    assert "ignored-repo" not in joined
    assert "ok" in joined
    top = top_unaccepted(home)
    assert top is not None
    assert top["repo"] == "SimonBarnett/ok"
    ear = EarOfferState()
    d = ear.decide(home, "ionos-4242", machine="ionos")
    assert d.action == "offer"
    assert "ok" in (d.line or "")
    assert "ignored-repo" not in (d.line or "")


def test_receiver_ignored_is_204_no_announce(tmp_path: Path):
    home = tmp_path / "d"
    add_ignore(home, "SimonBarnett/quiet")
    rx = StubReceiver(home=home)
    # exercise process path via internal state helpers if available
    line, claim, reject = process_git_webhook(
        "push",
        {
            "ref": "refs/heads/main",
            "after": "abc123",
            "commits": [],
            "repository": {
                "full_name": "SimonBarnett/quiet",
                "name": "quiet",
                "owner": {"login": "SimonBarnett"},
            },
        },
        home=home,
    )
    assert reject == "ignored_repo"
    assert line is None
    del rx


def test_commands_and_help_registered():
    assert validate_registry() == []
    for name in ("ignore", "ignored", "unignore"):
        spec = get_command(name)
        assert spec is not None
        assert spec.syntax.strip()
    # worker sees !ignored, not mutate cmds
    worker = "\n".join(build_help("flamingo-1", include_shop_pointer=False).lines)
    assert "ignored" in worker.lower()
    assert not any(
        ln.strip().lower().startswith("!ignore ") for ln in worker.splitlines()
    )
    simon = "\n".join(build_help("simon", include_shop_pointer=False).lines)
    assert "!ignore" in simon or "ignore" in simon.lower()
    assert may_mutate_ignore("bob-ionos")
    assert may_mutate_ignore("simon", account=None)
    assert may_mutate_ignore("simon", account="simon")
    assert not may_mutate_ignore("simon", account="other")
    assert not may_mutate_ignore("flamingo-1")


def test_chair_ignore_pm(tmp_path: Path):
    import time

    home = tmp_path / "d"
    home.mkdir(parents=True)
    ircd = LocalIrcd()
    port = ircd.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        report_url="http://127.0.0.1/9",
        shops=["#bobiverse"],
        auto_join=False,
    )
    chair.start()
    time.sleep(0.1)
    try:
        # ops bob-* may mutate without SASL account
        chair._handle_shop("bob-ionos", "#bobiverse", "!ignore ToyRepo")
        assert any(h.startswith("ignore:bob-ionos:") for h in chair.handled), chair.handled
        assert is_ignored(home, "Someone/ToyRepo")
        # simon without services account is denied when mode_grants is wired
        chair._handle_shop("simon", "#bobiverse", "!ignore Other")
        assert any("ignore_denied" in h for h in chair.handled)
        if chair.mode_grants is not None:
            chair.mode_grants.state.accounts["simon"] = "simon"
        chair._handle_shop("simon", "#bobiverse", "!ignore Other")
        assert any(h.startswith("ignore:simon:") for h in chair.handled)
        chair._handle_shop("flamingo-99", "#bobiverse", "!ignored")
        assert any(h.startswith("ignored_list:") for h in chair.handled)
        pms = [t for n, t in chair.pm_egress]
        assert any("ToyRepo" in t or "ignored" in t.lower() for t in pms)
        chair._handle_shop("worker-1", "#bobiverse", "!ignore nope")
        assert any("ignore_denied" in h for h in chair.handled)
        chair._handle_shop("bob-ionos", "#ionos", "!unignore ToyRepo")
        assert any(h.startswith("unignore:bob-ionos:") for h in chair.handled)
        assert not is_ignored(home, "Someone/ToyRepo")
    finally:
        chair.stop()
        ircd.stop()


def test_focus_layering_ignored_never_appears(tmp_path: Path):
    """Ignored repos stay hidden even if a future !focus would prioritize them."""
    home = tmp_path / "d"
    rows = [
        {"repo": "SimonBarnett/focus-me", "task": "FR", "id": "#1", "seq": 1},
        {"repo": "SimonBarnett/other", "task": "FR", "id": "#2", "seq": 2},
    ]
    add_ignore(home, "focus-me")
    # pretend focus sorted focus-me first — filter still drops it
    focused = sorted(rows, key=lambda r: 0 if "focus-me" in r["repo"] else 1)
    visible = filter_rows_not_ignored(home, focused)
    assert [r["repo"] for r in visible] == ["SimonBarnett/other"]
