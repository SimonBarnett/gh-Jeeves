"""FR #26: webhook intake for people with no GitHub account (zero AI tokens)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.intake import (
    DEFAULT_ALLOW_REPOS,
    FakeGitHubFiler,
    GitHubDown,
    IntakeConfig,
    RateLimiter,
    drain_intake_outbox,
    get_intake_status,
    harvest_should_use_intake,
    process_intake,
    validate_payload,
)
from jeeves.receiver import StubReceiver


def _post(url: str, payload: dict, *, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            obj = json.loads(body) if body else {}
        except json.JSONDecodeError:
            obj = {"raw": body}
        return e.code, obj


def _get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            obj = json.loads(body) if body else {}
        except json.JSONDecodeError:
            obj = {}
        return e.code, obj


def test_validate_rejects_disallowed_repo():
    err, _ = validate_payload(
        {
            "kind": "fr",
            "repo": "Evil/not-allowed",
            "title": "x",
            "body": "y",
        }
    )
    assert err == "repo_not_allowed"


def test_validate_size_cap():
    err, _ = validate_payload(
        {
            "kind": "issue",
            "repo": "SimonBarnett/gh-Jeeves",
            "title": "big",
            "body": "x" * (300 * 1024),
        }
    )
    assert err == "payload_too_large"


def test_issue_fr_filed_via_intake(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    filer = FakeGitHubFiler()
    res = process_intake(
        home,
        {
            "kind": "fr",
            "repo": "SimonBarnett/gh-Jeeves",
            "title": "FR: no gh box",
            "body": "need harvest path",
            "source": {"machine": "field1", "agent": "seat", "skill_book": "gh-Jeeves"},
            "idempotency_key": "k-fr-1",
        },
        filer=filer,
        client_ip="127.0.0.1",
    )
    assert res.status == 202
    assert res.body.get("url")
    assert res.body.get("intake_id")
    assert filer.issues
    assert "via-intake" in filer.issues[0]["labels"]
    assert "feature-request" in filer.issues[0]["labels"]
    assert "contact" not in (res.log_safe or "")
    assert "need harvest" not in (res.log_safe or "")


def test_skill_harvest_creates_draft_pr(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    filer = FakeGitHubFiler()
    res = process_intake(
        home,
        {
            "kind": "harvest",
            "repo": "SimonBarnett/gh-Jeeves",
            "title": "harvest: intake skill",
            "body": "playbook",
            "files": [
                {"path": "skills/harvest/SKILL.md", "content": "# harvest\n"},
            ],
            "source": {"machine": "laptop"},
            "idempotency_key": "k-har-1",
        },
        filer=filer,
    )
    assert res.status == 202
    assert filer.prs
    assert filer.prs[0]["draft"] is True
    assert filer.prs[0]["files"][0]["path"] == "skills/harvest/SKILL.md"
    assert "skill" in filer.prs[0]["labels"]


def test_idempotency_key_one_issue(tmp_path: Path):
    home = tmp_path / "h3"
    home.mkdir()
    filer = FakeGitHubFiler()
    payload = {
        "kind": "issue",
        "repo": "SimonBarnett/gh-Jeeves",
        "title": "once",
        "body": "body",
        "idempotency_key": "same-key-99",
    }
    a = process_intake(home, payload, filer=filer)
    b = process_intake(home, payload, filer=filer)
    assert a.status == b.status == 202
    assert a.body["intake_id"] == b.body["intake_id"]
    assert b.body.get("duplicate") is True
    assert len(filer.issues) == 1


def test_github_down_queues_then_drain(tmp_path: Path):
    home = tmp_path / "h4"
    home.mkdir()
    filer = FakeGitHubFiler(down=True)
    res = process_intake(
        home,
        {
            "kind": "issue",
            "repo": "SimonBarnett/gh-Jeeves",
            "title": "queued",
            "body": "wait",
            "idempotency_key": "q-1",
        },
        filer=filer,
    )
    assert res.status == 202
    assert res.body.get("queued") is True
    iid = res.body["intake_id"]
    code, st = get_intake_status(home, iid)
    assert code == 200 and st.get("queued") is True
    filer.down = False
    filed = drain_intake_outbox(home, filer)
    assert iid in filed
    code2, st2 = get_intake_status(home, iid)
    assert st2.get("url")
    assert st2.get("queued") is False


def test_rate_limit_429(tmp_path: Path):
    home = tmp_path / "h5"
    home.mkdir()
    filer = FakeGitHubFiler()
    cfg = IntakeConfig(rate_per_min=3)
    rate = RateLimiter(3)
    payload = {
        "kind": "issue",
        "repo": "SimonBarnett/gh-Jeeves",
        "title": "r",
        "body": "b",
    }
    codes = []
    for i in range(5):
        r = process_intake(
            home,
            {**payload, "idempotency_key": f"rl-{i}"},
            filer=filer,
            cfg=cfg,
            rate=rate,
            client_ip="10.0.0.9",
            now=1_000_000.0,
        )
        codes.append(r.status)
    assert codes.count(202) == 3
    assert codes.count(429) == 2


def test_http_receiver_intake_end_to_end(tmp_path: Path):
    home = tmp_path / "rx"
    home.mkdir()
    rx = StubReceiver(home, intake_cfg=IntakeConfig(fleet_key="fleet-secret"))
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # no key → quarantine labels still file
        code, body = _post(
            f"{base}/bob/v1/intake",
            {
                "kind": "fr",
                "repo": "SimonBarnett/gh-Jeeves",
                "title": "from field",
                "body": "no gh",
                "source": {"machine": "field"},
                "idempotency_key": "http-1",
            },
        )
        assert code == 202
        assert body.get("url")
        assert any("via-intake-untriaged" in i["labels"] for i in rx.state.intake_filer.issues)
        # keyed
        code2, body2 = _post(
            f"{base}/bob/v1/intake",
            {
                "kind": "issue",
                "repo": "SimonBarnett/gh-Jeeves",
                "title": "keyed",
                "body": "ok",
                "idempotency_key": "http-2",
            },
            headers={"X-Bob-Intake-Key": "fleet-secret"},
        )
        assert code2 == 202
        iid = body2["intake_id"]
        gcode, gst = _get(f"{base}/bob/v1/intake/{iid}")
        assert gcode == 200 and gst.get("url")
        # disallow repo
        code3, body3 = _post(
            f"{base}/bob/v1/intake",
            {"kind": "issue", "repo": "Nope/nope", "title": "x", "body": "y"},
        )
        assert code3 == 403
        assert body3.get("error") == "repo_not_allowed"
        # logs never carry contact
        assert all("secret-contact" not in x for x in rx.state.intake_logs)
        _post(
            f"{base}/bob/v1/intake",
            {
                "kind": "issue",
                "repo": "SimonBarnett/gh-Jeeves",
                "title": "c",
                "body": "b",
                "contact": "secret-contact@example.com",
                "idempotency_key": "http-3",
            },
            headers={"X-Bob-Intake-Key": "fleet-secret"},
        )
        assert all("secret-contact" not in x for x in rx.state.intake_logs)
        # public issue body must not include contact unless contact_public
        last = rx.state.intake_filer.issues[-1]
        assert "secret-contact" not in last["body"]
    finally:
        rx.stop()


def test_harvest_route_order():
    assert harvest_should_use_intake(gh_available=True, gh_authenticated=True) is False
    assert harvest_should_use_intake(gh_available=True, gh_authenticated=False) is True
    assert harvest_should_use_intake(gh_available=False, gh_authenticated=False) is True


def test_no_network_llm_imports_in_intake():
    src = (Path(__file__).resolve().parents[1] / "src" / "jeeves" / "intake.py").read_text(
        encoding="utf-8"
    )
    for banned in ("openai", "anthropic", "requests", "httpx"):
        assert banned not in src
