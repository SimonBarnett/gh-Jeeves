"""bobiverse#168: chair/outbox FRs must not assign to marchhare — only ionos.

Evidence: Jeeves assigned bobiverse#118 (chair GIT announce / chair-outbox) to
marchhare-41912; fix needs ionos ircJeeves. Prefer machine-capability routing.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.capability import (
    CHAIR_HOST_MACHINE,
    infer_require_machine,
    machine_of_nick,
    row_blocked_for_machine,
)
from jeeves.queue import claim_from_payload, load_queue, save_queue


def test_infer_require_machine_from_title_and_labels():
    assert (
        infer_require_machine(
            title="git ping 204 but no Jeeves GIT line (chair outbox?)",
            body="Confirm Jeeves drains chair-outbox",
            labels=[],
        )
        == CHAIR_HOST_MACHINE
    )
    assert (
        infer_require_machine(
            title="unrelated FR",
            body="",
            labels=[{"name": "needs-ionos"}],
        )
        == CHAIR_HOST_MACHINE
    )
    assert (
        infer_require_machine(
            title="fix TipForm Restart menu",
            body="tray only",
            labels=["via-intake"],
        )
        is None
    )


def test_machine_of_nick():
    assert machine_of_nick("marchhare-41912") == "marchhare"
    assert machine_of_nick("ionos-14020") == "ionos"
    assert machine_of_nick("Jeeves") is None


def test_row_blocked_for_wrong_machine():
    row = {
        "task": "FR",
        "repo": "SimonBarnett/bobiverse",
        "id": "#118",
        "line": "git ping 204 chair-outbox",
        "require_machine": "ionos",
    }
    assert row_blocked_for_machine(row, "marchhare-41912")
    assert not row_blocked_for_machine(row, "ionos-14020")
    assert not row_blocked_for_machine(
        {"task": "FR", "id": "#1", "line": "normal"}, "marchhare-41912"
    )


def test_assign_skips_chair_job_on_marchhare_offers_to_ionos(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/bobiverse",
                    "task": "FR",
                    "id": "#118",
                    "line": "git ping 204 but no Jeeves GIT line (chair outbox?)",
                    "url": "https://github.com/SimonBarnett/bobiverse/issues/118",
                    "require_machine": "ionos",
                    "seq": 1,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d_mh = st.decide(
        home,
        "marchhare-41912",
        "#marchhare",
        live_nicks={"marchhare-41912", "ionos-14020"},
    )
    assert d_mh.action == "nothing"
    assert "nothing queued" in (d_mh.line or "")

    d_io = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"marchhare-41912", "ionos-14020"},
    )
    assert d_io.action == "assign"
    assert "#118" in (d_io.line or "")
    assert "FR" in (d_io.line or "")


def test_assign_infers_capability_from_line_without_require_field(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/bobiverse",
                    "task": "FR",
                    "id": "#118",
                    "line": "Confirm ircJeeves drains chair-outbox after webhook 204",
                    "url": "https://github.com/SimonBarnett/bobiverse/issues/118",
                    "seq": 1,
                },
                {
                    "repo": "SimonBarnett/bobiverse",
                    "task": "FR",
                    "id": "#200",
                    "line": "TipForm menu label tweak",
                    "url": "https://github.com/SimonBarnett/bobiverse/issues/200",
                    "seq": 2,
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
        "marchhare-41912",
        "#marchhare",
        live_nicks={"marchhare-41912"},
    )
    # Skip #118 (chair); offer #200
    assert d.action == "assign"
    assert "#200" in (d.line or "")


def test_claim_from_issue_stamps_require_machine():
    claim = claim_from_payload(
        "issues",
        {
            "action": "opened",
            "repository": {"full_name": "SimonBarnett/bobiverse"},
            "issue": {
                "number": 118,
                "title": "git ping 204 but no Jeeves GIT line (chair outbox?)",
                "body": "Fix needs ionos ircJeeves/chair-outbox.",
                "html_url": "https://github.com/SimonBarnett/bobiverse/issues/118",
                "labels": [{"name": "via-intake"}],
            },
        },
    )
    assert claim is not None
    assert claim.task == "FR"
    assert claim.require_machine == "ionos"
