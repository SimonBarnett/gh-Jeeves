"""FR #134: DONE FR with PR URL supersedes FR by MRB; focus follows; no re-offer."""
from __future__ import annotations

from pathlib import Path

from jeeves.focus import item_focus_map, load_focus, set_item_focus
from jeeves.queue import (
    Claim,
    accept_job,
    apply_queue_event,
    complete_job,
    load_queue,
    ordered_unaccepted,
    save_queue,
)


REPO = "SimonBarnett/AgentMonitor"


def _seed_fr_accepted(home: Path, fr: str = "#103", nick: str = "marchhare-31712") -> None:
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": REPO,
                    "task": "FR",
                    "id": fr,
                    "nick": nick,
                    "channel": "#marchhare",
                    "line": f"FR {fr}",
                    "url": f"https://github.com/{REPO}/issues/{fr.lstrip('#')}",
                }
            ],
            "done": [],
            "workers": {nick: {"state": "busy"}},
        },
    )


def test_done_fr_with_pr_url_supersedes_to_mrb(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _seed_fr_accepted(home)
    st, row = complete_job(
        home,
        "marchhare-31712",
        "FR",
        REPO,
        "103",
        "ok",
        "https://github.com/SimonBarnett/AgentMonitor/pull/107",
    )
    assert st == "done"
    q = load_queue(home)
    assert not any(
        str(r.get("task")).upper() == "FR" and str(r.get("id")) in ("#103", "103")
        for r in (q["unaccepted"] + q["accepted"])
    )
    mrb = [
        r
        for r in q["unaccepted"]
        if str(r.get("task")).upper() == "MRB" and str(r.get("id")) in ("#107", "107")
    ]
    assert len(mrb) == 1
    assert "#103" in [str(x) for x in (mrb[0].get("refs") or [])] or "103" in str(
        mrb[0].get("refs")
    )
    ordered = ordered_unaccepted(home)
    assert ordered[0]["task"] == "MRB"
    assert ordered[0]["id"] in ("#107", "107")


def test_open_pr_claim_suppresses_fr(tmp_path: Path):
    home = tmp_path / "d2"
    home.mkdir()
    apply_queue_event(
        home,
        Claim(repo=REPO, task="FR", id="#103", line="loop seat", url="https://x/issues/103"),
    )
    assert any(r["task"] == "FR" for r in load_queue(home)["unaccepted"])
    apply_queue_event(
        home,
        Claim(
            repo=REPO,
            task="MRB",
            id="#107",
            line="feat(#103) Closes #103",
            url="https://github.com/SimonBarnett/AgentMonitor/pull/107",
            refs=("#103",),
            pr_id="#107",
        ),
    )
    q = load_queue(home)
    assert not any(str(r.get("task")).upper() == "FR" for r in q["unaccepted"])
    assert any(str(r.get("task")).upper() == "MRB" for r in q["unaccepted"])
    # Re-opened FR webhook must not put FR back while MRB open
    tag = apply_queue_event(
        home,
        Claim(repo=REPO, task="FR", id="#103", action="reopened", line="still open"),
    )
    assert "superseded" in tag or tag.startswith("skipped")
    q2 = load_queue(home)
    assert not any(str(r.get("task")).upper() == "FR" for r in q2["unaccepted"])


def test_pr_closed_unmerged_restores_fr(tmp_path: Path):
    home = tmp_path / "d3"
    home.mkdir()
    apply_queue_event(
        home,
        Claim(
            repo=REPO,
            task="MRB",
            id="#107",
            line="Closes #103",
            refs=("#103",),
            pr_id="#107",
        ),
    )
    apply_queue_event(
        home,
        Claim(
            repo=REPO,
            task="RESTORE_FR",
            id="#107",
            line="Closes #103",
            refs=("#103",),
            pr_id="#107",
        ),
    )
    q = load_queue(home)
    assert not any(str(r.get("task")).upper() == "MRB" for r in q["unaccepted"])
    assert any(
        str(r.get("task")).upper() == "FR" and str(r.get("id")) in ("#103", "103")
        for r in q["unaccepted"]
    )


def test_focus_on_fr_moves_to_mrb_on_done(tmp_path: Path):
    home = tmp_path / "d4"
    home.mkdir()
    set_item_focus(home, f"{REPO}#103", rank=1)
    _seed_fr_accepted(home)
    complete_job(
        home,
        "marchhare-31712",
        "FR",
        REPO,
        "103",
        "ok",
        "https://github.com/SimonBarnett/AgentMonitor/pull/107",
    )
    items = item_focus_map(home)
    keys = list(items.keys())
    assert any(k.lower().endswith("#107") for k in keys)
    assert not any(k.lower().endswith("#103") for k in keys)
