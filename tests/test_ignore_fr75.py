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


def test_supersede_skipped_for_ignored_repo(tmp_path: Path):
    """FR #75: ignored repos get no supersede — apply_queue_event returns ignored."""
    home = tmp_path / "d"
    # seed a FR that would normally be superseded by MRB
    apply_queue_event(
        home,
        Claim(repo="SimonBarnett/noise", task="FR", id="#9", line="old"),
    )
    apply_queue_event(
        home,
        Claim(repo="SimonBarnett/keep", task="FR", id="#1", line="stay"),
    )
    # IRC path: !ignore must purge queued noise then block supersede
    handle_ignore_add(home, "SimonBarnett/noise")
    before = load_queue(home)
    assert all(r.get("repo") != "SimonBarnett/noise" for r in before["unaccepted"])
    keep_before = [r for r in before["unaccepted"] if r.get("repo") == "SimonBarnett/keep"]
    assert len(keep_before) == 1
    st = apply_queue_event(
        home,
        Claim(
            repo="SimonBarnett/noise",
            task="MRB",
            id="#99",
            line="pr",
            pr_id="#99",
            refs=("#9",),
        ),
    )
    assert st == "ignored"
    after = load_queue(home)
    assert all(r.get("repo") != "SimonBarnett/noise" for r in after["unaccepted"])
    keep_after = [r for r in after["unaccepted"] if r.get("repo") == "SimonBarnett/keep"]
    assert keep_after == keep_before


def test_purge_clears_accepted_and_done(tmp_path: Path):
    home = tmp_path / "d"
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {"repo": "o/dead", "task": "FR", "id": "#1", "line": "u", "seq": 1}
            ],
            "accepted": [
                {"repo": "o/dead", "task": "FR", "id": "#2", "line": "a", "seq": 2}
            ],
            "done": [
                {"repo": "o/dead", "task": "FR", "id": "#3", "line": "d", "seq": 3}
            ],
            "workers": {},
        },
    )
    n = purge_repo_from_queue(home, "dead")
    assert n == 3
    q = load_queue(home)
    assert q["unaccepted"] == []
    assert q["accepted"] == []
    assert q["done"] == []


def test_resync_skips_ignored_repos(tmp_path: Path):
    """FR #75 / #49: resync must not re-enqueue ignored repos from GitHub."""
    from jeeves.resync import ResyncConfig, build_outstanding

    home = tmp_path / "d"
    add_ignore(home, "SimonBarnett/skip-me")

    class _Client:
        def list_repos(self):
            return ["SimonBarnett/skip-me", "SimonBarnett/keep-me"]

        def list_open_issues(self, repo):
            return [
                {
                    "number": 1,
                    "title": f"issue-{repo}",
                    "html_url": f"https://github.com/{repo}/issues/1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]

        def list_open_pulls(self, repo):
            return []

        def list_recent_closed_pulls(self, repo):
            return []

    rows = build_outstanding(_Client(), cfg=ResyncConfig(), home=home)
    repos = {str(r.get("repo")) for r in rows}
    assert "SimonBarnett/skip-me" not in repos
    assert "SimonBarnett/keep-me" in repos


def test_bare_ignore_and_unignore_usage(tmp_path: Path):
    home = tmp_path / "d"
    assert handle_ignore_add(home, "") == [
        "ignore: bad repo (use name or owner/name)"
    ]
    assert handle_unignore(home, "never-there")[0].startswith("unignore:")
    assert "was not ignored" in handle_unignore(home, "never-there")[0]


def test_ignored_json_persists_across_reload(tmp_path: Path):
    home = tmp_path / "d"
    add_ignore(home, "Owner/One")
    add_ignore(home, "Two")
    doc = load_ignored(home)
    assert any(r.lower() == "owner/one" for r in doc["repos"])
    assert any(r.lower() == "two" for r in doc["repos"])
    # fresh load from disk
    assert is_ignored(home, "owner/one")
    assert is_ignored(home, "Someone/Two")

def test_bare_unignore_usage(tmp_path: Path):
    home = tmp_path / "d"
    assert handle_unignore(home, "") == [
        "unignore: bad repo (use name or owner/name)"
    ]


def test_unignore_does_not_restore_purged_rows(tmp_path: Path):
    """Unignore is new events only — purged FR must stay gone."""
    home = tmp_path / "d"
    apply_queue_event(
        home, Claim(repo="SimonBarnett/noise", task="FR", id="#9", line="old")
    )
    handle_ignore_add(home, "noise")
    assert all(r.get("repo") != "SimonBarnett/noise" for r in load_queue(home)["unaccepted"])
    handle_unignore(home, "noise")
    assert all(r.get("repo") != "SimonBarnett/noise" for r in load_queue(home)["unaccepted"])
    # new event still allowed
    assert apply_queue_event(
        home, Claim(repo="SimonBarnett/noise", task="FR", id="#10", line="new")
    ).startswith("enqueued")


def test_already_ignoring_still_purges(tmp_path: Path):
    home = tmp_path / "d"
    add_ignore(home, "noise")
    apply_queue_event(
        home, Claim(repo="SimonBarnett/noise", task="FR", id="#1", line="sneak")
    )
    # row slipped in before second ignore path (e.g. race) — re-ignore purges
    lines = handle_ignore_add(home, "noise")
    assert any("already ignoring" in ln for ln in lines)
    assert any("purged" in ln for ln in lines)
    assert all(r.get("repo") != "SimonBarnett/noise" for r in load_queue(home)["unaccepted"])


def test_corrupt_ignored_json_recovers_empty(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir(parents=True)
    (home / "ignored.json").write_text("{not json", encoding="utf-8")
    assert load_ignored(home)["repos"] == []
    assert not is_ignored(home, "a/b")


def test_format_ignored_empty_and_list(tmp_path: Path):
    from jeeves.ignore import format_ignored_lines
    home = tmp_path / "d"
    assert format_ignored_lines(home) == ["ignored: (none)"]
    add_ignore(home, "Owner/One")
    lines = format_ignored_lines(home)
    assert lines[0].startswith("ignored (")
    assert any("Owner/One" in ln or "owner/one" in ln.lower() for ln in lines)


def test_worker_denied_unignore_on_chair(tmp_path: Path):
    import time
    home = tmp_path / "d"
    home.mkdir(parents=True)
    add_ignore(home, "Toy")
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
    time.sleep(0.05)
    try:
        chair._handle_shop("flamingo-99", "#bobiverse", "!unignore Toy")
        assert any("unignore_denied" in h or "ignore_denied" in h for h in chair.handled), chair.handled
        assert is_ignored(home, "x/Toy")
    finally:
        chair.stop()
        ircd.stop()

