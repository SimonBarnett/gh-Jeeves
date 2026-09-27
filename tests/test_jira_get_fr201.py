"""FR #201: GET /bob/v1/jira — read saved tickets with X-Bob-Secret."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from jeeves.jira_webhook import process_jira_webhook, save_jira_tickets
from jeeves.receiver import StubReceiver

SECRET = "test-jira-get-fr201"


def _get(
    url: str,
    *,
    secret: str | None = SECRET,
    path: str = "/bob/v1/jira",
) -> tuple[int, bytes]:
    headers: dict[str, str] = {}
    if secret is not None:
        headers["X-Bob-Secret"] = secret
    req = urllib.request.Request(
        url.rstrip("/") + path, headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post_issue(url: str, key: str = "GET-1") -> int:
    data = json.dumps(
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": key,
                "fields": {
                    "summary": f"Summary {key}",
                    "description": "d",
                    "status": {"name": "Open"},
                    "assignee": {"displayName": "Si"},
                    "project": {"key": "GET"},
                },
            },
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Bob-Secret": SECRET,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/bob/v1/jira", data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_get_missing_and_wrong_secret_401(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        code, _ = _get(rx.url, secret=None)
        assert code == 401
        code, _ = _get(rx.url, secret="nope")
        assert code == 401
    finally:
        rx.stop()


def test_get_empty_file_200(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        code, body = _get(rx.url)
        assert code == 200
        obj = json.loads(body.decode("utf-8"))
        assert obj == {"tickets": {}, "updated_at": ""}
    finally:
        rx.stop()


def test_get_populated_after_post(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        assert _post_issue(rx.url, "GET-42") == 204
        code, body = _get(rx.url)
        assert code == 200
        obj = json.loads(body.decode("utf-8"))
        assert "GET-42" in obj["tickets"]
        assert obj["tickets"]["GET-42"]["summary"] == "Summary GET-42"
        assert isinstance(obj.get("updated_at"), str)
        # Never echo the shared secret in the JSON body.
        assert SECRET not in body.decode("utf-8")
    finally:
        rx.stop()


def test_get_preseeded_tickets_json(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_jira_tickets(
        home,
        {
            "tickets": {"PRE-1": {"key": "PRE-1", "summary": "preseed"}},
            "updated_at": "2026-09-27T00:00:00Z",
        },
    )
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        code, body = _get(rx.url)
        assert code == 200
        obj = json.loads(body.decode("utf-8"))
        assert obj["tickets"]["PRE-1"]["summary"] == "preseed"
        assert obj["updated_at"] == "2026-09-27T00:00:00Z"
    finally:
        rx.stop()


def test_get_disarmed_secret_ok(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    process_jira_webhook(
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "Z-9",
                "fields": {
                    "summary": "z",
                    "status": {"name": "Open"},
                    "project": {"key": "Z"},
                },
            },
        },
        home=home,
    )
    rx = StubReceiver(home, bob_secret="", require_secret=False)
    rx.start()
    try:
        code, body = _get(rx.url, secret=None)
        assert code == 200
        assert "Z-9" in json.loads(body.decode("utf-8"))["tickets"]
    finally:
        rx.stop()


def test_post_jira_unchanged_still_204(tmp_path: Path):
    """FR #201 non-goal: POST /bob/v1/jira behaviour unchanged."""
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    rx.start()
    try:
        assert _post_issue(rx.url, "POST-1") == 204
    finally:
        rx.stop()
