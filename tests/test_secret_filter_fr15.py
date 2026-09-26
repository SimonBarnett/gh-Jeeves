"""FR #15 / K14: secret filter must not reject whole payloads (agentic_irc #206).

Scan only secret-bearing fields. Issues that *discuss* markers (brief text,
connect.password prose, password= in titles) must still announce. Malformed
requests stay 400 and are logged.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.announce import process_git_webhook
from jeeves.receiver import StubReceiver

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "docs" / "brief" / "JEEVES_BRIEF.md"


def _post_git(
    base: str,
    payload: dict | bytes,
    *,
    event: str | None = "issues",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    data = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if event is not None:
        hdrs["X-GitHub-Event"] = event
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(base + "/bob/v1/git", data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_brief_body_discussing_markers_announces(tmp_path: Path):
    """Seed FR: test with this brief's text — must 204 and announce, never 400."""
    home = tmp_path / "d"
    home.mkdir()
    brief = BRIEF.read_text(encoding="utf-8")
    assert "K14" in brief
    rx = StubReceiver(home)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        payload = {
            "action": "opened",
            "issue": {
                "number": 15,
                "title": "FR: K14 receiver secret filter rejects whole payloads",
                "body": brief,
                "html_url": "https://github.com/SimonBarnett/gh-Jeeves/issues/15",
            },
            "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
            "sender": {"login": "bob"},
        }
        code, _ = _post_git(base, payload)
        assert code == 204
        assert rx.state.announces, "expected GIT announce for brief-bodied issue"
        line = rx.state.announces[-1]
        assert "SimonBarnett/gh-Jeeves#15" in line or "gh-Jeeves#15" in line
        assert "K14" in line
        out = (home / "chair-outbox.txt").read_text(encoding="utf-8")
        assert "PRIVMSG #bobiverse :" in out
        assert line in out
    finally:
        rx.stop()


def test_body_mentions_connect_password_announces(tmp_path: Path):
    """agentic_irc #206: body mentioning connect.password must not drop the event."""
    home = tmp_path / "d"
    home.mkdir()
    # Build at runtime so a naive whole-JSON scan still sees the marker substring.
    marker = "connect" + ".password"
    body = f"same handling as `{marker}` from the docs (not a secret)."
    line, claim, reject = process_git_webhook(
        "issues",
        {
            "action": "opened",
            "issue": {
                "number": 327,
                "title": "docs path mention",
                "body": body,
                "html_url": "https://github.com/SimonBarnett/agentic_build/issues/327",
            },
            "repository": {"full_name": "SimonBarnett/agentic_build"},
        },
    )
    assert reject is None
    assert claim is not None
    assert line is not None
    assert "docs path mention" in line


def test_title_password_eq_redacts_not_400(tmp_path: Path):
    """Title containing password= → 204 with redacted announce; value never in outbox."""
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        secretish = "password=" + "hunter2"
        payload = {
            "action": "opened",
            "issue": {
                "number": 328,
                "title": f"docs example {secretish}",
                "body": "prose only",
                "html_url": "https://github.com/o/r/issues/328",
            },
            "repository": {"full_name": "o/r"},
        }
        code, _ = _post_git(base, payload)
        assert code == 204
        assert rx.state.announces
        line = rx.state.announces[-1]
        assert "hunter2" not in line
        assert "[redacted]" in line or "redacted" in line.lower()
        out = (home / "chair-outbox.txt").read_text(encoding="utf-8")
        assert "hunter2" not in out
    finally:
        rx.stop()


def test_missing_github_event_header_400_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        payload = {
            "action": "opened",
            "issue": {"number": 1, "title": "x", "body": "y", "html_url": "https://o/r/issues/1"},
            "repository": {"full_name": "o/r"},
        }
        with caplog.at_level(logging.WARNING, logger="jeeves.receiver"):
            code, _ = _post_git(base, payload, event=None)
        assert code == 400
        assert any("missing" in r.getMessage().lower() and "event" in r.getMessage().lower() for r in caplog.records)
    finally:
        rx.stop()


def test_invalid_json_400_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with caplog.at_level(logging.WARNING, logger="jeeves.receiver"):
            code, _ = _post_git(base, b"{not-json", event="issues")
        assert code == 400
        assert any("json" in r.getMessage().lower() for r in caplog.records)
    finally:
        rx.stop()


def test_secret_bearing_field_still_rejected():
    """K14: still scan secret-bearing fields — token/password values with markers reject."""
    line, claim, reject = process_git_webhook(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 2, "title": "x", "body": "y", "html_url": "https://o/r/issues/2"},
            "repository": {"full_name": "o/r"},
            "token": "ghp_" + "REALLOOKINGTOKENVALUE12",
        },
    )
    assert reject is not None
    assert line is None
    assert claim is None
