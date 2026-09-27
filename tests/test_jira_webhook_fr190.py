"""FR #190: POST /bob/v1/jira — native Jira payload, secret, persist, announce."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.jira_webhook import (
    extract_issue_record,
    format_jira_announce,
    iter_issues_from_payload,
    load_jira_tickets,
    process_jira_webhook,
)
from jeeves.receiver import StubReceiver

SECRET = "test-jira-secret-fr190"


def _post(
    url: str,
    payload: dict | bytes,
    *,
    secret: str | None = SECRET,
    path: str = "/bob/v1/jira",
) -> tuple[int, bytes]:
    if isinstance(payload, dict):
        data = json.dumps(payload).encode("utf-8")
    else:
        data = payload
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Bob-Secret"] = secret
    req = urllib.request.Request(
        url.rstrip("/") + path, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _issue(
    key: str = "PROJ-123",
    summary: str = "Fix login timeout",
    description: str = "Users logged out too soon.",
    status: str = "In Progress",
    assignee: str = "Simon Barnett",
    project: str = "PROJ",
) -> dict:
    return {
        "id": "10042",
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "status": {"name": status},
            "assignee": {"displayName": assignee},
            "project": {"key": project},
            "labels": ["mobile"],
            "unknown_extra": {"nested": True},
        },
    }


def test_extract_single_and_bulk_and_unknown_fields():
    single = {
        "webhookEvent": "jira:issue_created",
        "timestamp": 1705424400000,
        "issue": _issue(),
        "totally_unknown": 1,
    }
    recs = iter_issues_from_payload(single)
    assert len(recs) == 1
    assert recs[0]["key"] == "PROJ-123"
    assert recs[0]["summary"] == "Fix login timeout"
    assert recs[0]["status"] == "In Progress"
    assert "unknown_extra" not in recs[0]

    bulk = {
        "webhookEvent": "jira:issue_updated",
        "timestamp": 1,
        "issues": [_issue("A-1", "One"), _issue("B-2", "Two")],
    }
    recs2 = iter_issues_from_payload(bulk)
    assert [r["key"] for r in recs2] == ["A-1", "B-2"]


def test_changelog_on_update():
    payload = {
        "webhookEvent": "jira:issue_updated",
        "timestamp": 2,
        "issue": _issue(),
        "changelog": {
            "items": [
                {
                    "field": "status",
                    "fromString": "To Do",
                    "toString": "In Progress",
                }
            ]
        },
    }
    rec = extract_issue_record(
        payload["issue"],
        webhook_event=payload["webhookEvent"],
        timestamp=payload["timestamp"],
        changelog=payload["changelog"],
    )
    assert rec is not None
    assert rec["changelog_items"][0]["to"] == "In Progress"
    line = format_jira_announce(rec)
    assert line.startswith("JIRA issue_updated PROJ-123")
    assert "Fix login timeout" in line


def test_process_persists_under_jira_dir(tmp_path: Path):
    payload = {
        "webhookEvent": "jira:issue_created",
        "timestamp": 3,
        "issue": _issue("MD-9", "Ship hours"),
    }
    lines, records, reject = process_jira_webhook(payload, home=tmp_path)
    assert reject is None
    assert len(records) == 1
    assert lines and "MD-9" in lines[0]
    doc = load_jira_tickets(tmp_path)
    assert "MD-9" in doc["tickets"]
    assert doc["tickets"]["MD-9"]["summary"] == "Ship hours"
    assert (tmp_path / "jira" / "tickets.json").is_file()


def test_receiver_create_update_bulk_auth_and_bad_json(tmp_path: Path):
    home = tmp_path / "digest"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        # missing secret → 401
        code, _ = _post(rx.url, {"issue": _issue()}, secret=None)
        assert code == 401
        # wrong secret → 401
        code, _ = _post(rx.url, {"issue": _issue()}, secret="nope")
        assert code == 401
        # malformed JSON → 400
        code, body = _post(rx.url, b"{not-json", secret=SECRET)
        assert code == 400
        assert b"invalid_json" in body

        # create
        code, _ = _post(
            rx.url,
            {
                "webhookEvent": "jira:issue_created",
                "timestamp": 10,
                "issue": _issue("PROJ-1", "Create me"),
            },
        )
        assert code == 204

        # update with changelog
        code, _ = _post(
            rx.url,
            {
                "webhookEvent": "jira:issue_updated",
                "timestamp": 11,
                "issue": _issue("PROJ-1", "Create me", status="Done"),
                "changelog": {
                    "items": [
                        {
                            "field": "status",
                            "fromString": "In Progress",
                            "toString": "Done",
                        }
                    ]
                },
            },
        )
        assert code == 204

        # bulk
        code, _ = _post(
            rx.url,
            {
                "webhookEvent": "jira:issue_updated",
                "timestamp": 12,
                "issues": [
                    _issue("PROJ-2", "Bulk A"),
                    _issue("PROJ-3", "Bulk B"),
                ],
            },
        )
        assert code == 204

        doc = load_jira_tickets(home)
        assert set(doc["tickets"]) >= {"PROJ-1", "PROJ-2", "PROJ-3"}
        assert doc["tickets"]["PROJ-1"]["status"] == "Done"

        outbox = (home / "chair-outbox.txt").read_text(encoding="utf-8")
        assert "PRIVMSG #bobiverse :JIRA" in outbox
        assert "PROJ-1" in outbox
        assert "PROJ-2" in outbox
        # secret never logged into outbox
        assert SECRET not in outbox
    finally:
        rx.stop()


def test_receiver_no_secret_required_when_disarmed(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    rx = StubReceiver(home, bob_secret="", require_secret=False)
    rx.start()
    try:
        code, _ = _post(
            rx.url,
            {"webhookEvent": "jira:issue_created", "issue": _issue("Z-1")},
            secret=None,
        )
        assert code == 204
        assert "Z-1" in load_jira_tickets(home)["tickets"]
    finally:
        rx.stop()


def test_adf_description_flattened():
    issue = _issue()
    issue["fields"]["description"] = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "ADF"}],
            }
        ],
    }
    rec = extract_issue_record(issue, webhook_event="jira:issue_created")
    assert rec is not None
    assert "Hello ADF" in rec["description"]
