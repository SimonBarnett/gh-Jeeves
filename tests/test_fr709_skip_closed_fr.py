"""FR #709: do not offer CLOSED / skip-label FRs; purge them at decide time."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.queue import (
    Claim,
    apply_queue_event,
    claim_from_payload,
    issue_skip_fr_reason,
    load_queue,
    row_skip_fr_reason,
    save_queue,
)


REPO = "SimonBarnett/bobiverse"


def test_issue_skip_fr_reason_closed_and_labels():
    assert issue_skip_fr_reason(title="x", state="closed") == "closed"
    assert issue_skip_fr_reason(title="x", labels=["mrb-home"]) == "label:mrb-home"
    assert issue_skip_fr_reason(title="harvest: foo") == "harvest_title"
    assert issue_skip_fr_reason(title="skill bar") == "harvest_title"
    # needs-mrb1 alone must NOT skip (intake stamps it on every FR)
    assert issue_skip_fr_reason(title="real FR", labels=["needs-mrb1", "via-intake"]) is None
    assert issue_skip_fr_reason(title="real FR", state="open") is None


def test_claim_from_payload_skips_closed_and_stamps_open():
    closed = claim_from_payload(
        "issues",
        {
            "action": "opened",
            "repository": {"full_name": REPO},
            "issue": {
                "number": 686,
                "title": "CRITICAL needs_human",
                "state": "closed",
                "html_url": f"https://github.com/{REPO}/issues/686",
                "labels": [{"name": "needs-mrb1"}],
            },
        },
    )
    assert closed is None
    opened = claim_from_payload(
        "issues",
        {
            "action": "opened",
            "repository": {"full_name": REPO},
            "issue": {
                "number": 709,
                "title": "chair offered CLOSED needs-mrb1 issue",
                "state": "open",
                "html_url": f"https://github.com/{REPO}/issues/709",
                "labels": [{"name": "needs-mrb1"}, {"name": "via-intake"}],
            },
        },
    )
    assert opened is not None
    assert opened.task == "FR"
    assert opened.state == "open"
    assert "needs-mrb1" in opened.labels


def test_decide_skips_and_purges_closed_fr(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "FR",
                    "id": "#686",
                    "line": "CRITICAL needs_human: UAT re-offer",
                    "url": f"https://github.com/{REPO}/issues/686",
                    "state": "closed",
                    "labels": ["needs-mrb1", "via-intake"],
                },
                {
                    "repo": REPO,
                    "task": "FR",
                    "id": "#710",
                    "line": "real open FR",
                    "url": f"https://github.com/{REPO}/issues/710",
                    "state": "open",
                },
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
        "marchhare-41928",
        "#marchhare",
        live_nicks={"marchhare-41928"},
    )
    assert d.action == "assign"
    assert "#710" in (d.line or "")
    q = load_queue(home)
    ids = [str(r.get("id")) for r in (q.get("unaccepted") or [])]
    assert "#686" not in ids
    assert "#710" in ids


def test_close_webhook_removes_fr(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    apply_queue_event(
        home,
        Claim(
            repo=REPO,
            task="FR",
            id="#686",
            event="issues",
            action="opened",
            line="x",
            state="open",
        ),
    )
    assert any(
        str(r.get("id")) == "#686" for r in load_queue(home).get("unaccepted") or []
    )
    tag = apply_queue_event(
        home,
        Claim(
            repo=REPO,
            task="CLOSE",
            id="#686",
            event="issues",
            action="closed",
            line="x",
            state="closed",
        ),
    )
    assert tag.startswith("removed:")
    assert not any(
        str(r.get("id")) == "#686" for r in load_queue(home).get("unaccepted") or []
    )


def test_row_skip_fr_reason_ignores_uat():
    assert (
        row_skip_fr_reason(
            {"task": "UAT", "id": "#1", "state": "closed", "line": "x"}
        )
        is None
    )
