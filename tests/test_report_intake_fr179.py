"""FR #179: report bugs/FRs via /bob/v1/intake when the user has no GitHub account."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.intake import (
    FakeGitHubFiler,
    GitHubDown,
    IntakeConfig,
    build_report_payload,
    drain_intake_outbox,
    get_intake_status,
    harvest_should_use_intake,
    process_intake,
    report_should_use_intake,
    write_local_report_outbox,
)
from jeeves.receiver import StubReceiver

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
REPORT_SKILL = SKILLS / "report" / "SKILL.md"


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


def test_build_report_payload_issue_and_fr():
    issue = build_report_payload(
        kind="issue",
        repo="SimonBarnett/gh-Jeeves",
        title="harvest: broken step",
        body="## What broke\n\nx",
        source={"machine": "field1", "agent": "seat", "skill_book": "gh-Jeeves"},
        idempotency_key="issue-field1-broken-step",
    )
    assert issue["kind"] == "issue"
    assert issue["repo"] == "SimonBarnett/gh-Jeeves"
    assert issue["idempotency_key"] == "issue-field1-broken-step"
    assert issue["source"]["machine"] == "field1"

    fr = build_report_payload(
        kind="fr",
        repo="SimonBarnett/gh-Jeeves",
        title="FR: add Z",
        body="## Why\n\ny",
        idempotency_key="fr-1",
    )
    assert fr["kind"] == "fr"
    assert "contact" not in fr

    with pytest.raises(ValueError):
        build_report_payload(kind="harvest", repo="a/b", title="t", body="b")


def test_report_route_selection_matches_harvest():
    assert report_should_use_intake(gh_available=True, gh_authenticated=True) is False
    assert report_should_use_intake(gh_available=True, gh_authenticated=False) is True
    assert report_should_use_intake(gh_available=False, gh_authenticated=False) is True
    assert report_should_use_intake(
        gh_available=False, gh_authenticated=False
    ) == harvest_should_use_intake(gh_available=False, gh_authenticated=False)


def test_write_local_report_outbox_strips_contact(tmp_path: Path):
    payload = build_report_payload(
        kind="issue",
        repo="SimonBarnett/gh-Jeeves",
        title="offline",
        body="wait",
        idempotency_key="offline-1",
        contact="secret-contact@example.com",
    )
    path = write_local_report_outbox(tmp_path / "report-outbox", payload)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["kind"] == "issue"
    assert saved["idempotency_key"] == "offline-1"
    assert "contact" not in saved
    # same key → stable filename for retry
    path2 = write_local_report_outbox(tmp_path / "report-outbox", payload)
    assert path2 == path


def test_issue_and_fr_labels_via_intake(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    filer = FakeGitHubFiler()

    issue_payload = build_report_payload(
        kind="issue",
        repo="SimonBarnett/gh-Jeeves",
        title="bug from skill",
        body="repro",
        source={"machine": "marchhare", "skill_book": "gh-Jeeves"},
        idempotency_key="fr179-issue-1",
    )
    fr_payload = build_report_payload(
        kind="fr",
        repo="SimonBarnett/gh-Jeeves",
        title="FR: from skill",
        body="wish",
        source={"machine": "marchhare", "skill_book": "gh-Jeeves"},
        idempotency_key="fr179-fr-1",
    )

    ri = process_intake(home, issue_payload, filer=filer)
    rf = process_intake(home, fr_payload, filer=filer)
    assert ri.status == rf.status == 202
    assert ri.body.get("url") and rf.body.get("url")
    assert len(filer.issues) == 2

    bug = next(i for i in filer.issues if i["title"] == "bug from skill")
    feat = next(i for i in filer.issues if i["title"] == "FR: from skill")
    assert "via-intake" in bug["labels"]
    assert "needs-mrb1" in bug["labels"]
    assert "feature-request" not in bug["labels"]
    assert "via-intake" in feat["labels"]
    assert "feature-request" in feat["labels"]
    assert "needs-mrb1" in feat["labels"]


def test_idempotency_same_key_one_issue(tmp_path: Path):
    home = tmp_path / "idem"
    home.mkdir()
    filer = FakeGitHubFiler()
    payload = build_report_payload(
        kind="issue",
        repo="SimonBarnett/gh-Jeeves",
        title="once",
        body="body",
        idempotency_key="fr179-same",
    )
    a = process_intake(home, payload, filer=filer)
    b = process_intake(home, payload, filer=filer)
    assert a.status == b.status == 202
    assert a.body["intake_id"] == b.body["intake_id"]
    assert b.body.get("duplicate") is True
    assert len(filer.issues) == 1


def test_disallowed_repo_4xx(tmp_path: Path):
    home = tmp_path / "deny"
    home.mkdir()
    filer = FakeGitHubFiler()
    payload = build_report_payload(
        kind="fr",
        repo="Evil/not-allowed",
        title="nope",
        body="x",
        idempotency_key="deny-1",
    )
    res = process_intake(home, payload, filer=filer)
    assert res.status == 403
    assert res.body.get("error") == "repo_not_allowed"
    assert filer.issues == []


def test_github_down_queues_local_then_drain(tmp_path: Path):
    home = tmp_path / "q"
    home.mkdir()
    filer = FakeGitHubFiler(down=True)
    payload = build_report_payload(
        kind="issue",
        repo="SimonBarnett/gh-Jeeves",
        title="queued",
        body="wait",
        idempotency_key="fr179-q-1",
    )
    res = process_intake(home, payload, filer=filer)
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


def test_http_receiver_issue_and_fr_no_secrets(tmp_path: Path):
    home = tmp_path / "rx"
    home.mkdir()
    rx = StubReceiver(home, intake_cfg=IntakeConfig(fleet_key="fleet-secret"))
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        issue = build_report_payload(
            kind="issue",
            repo="SimonBarnett/gh-Jeeves",
            title="keyed bug",
            body="ok",
            idempotency_key="fr179-http-issue",
            contact="secret-contact@example.com",
        )
        fr = build_report_payload(
            kind="fr",
            repo="SimonBarnett/gh-Jeeves",
            title="keyed fr",
            body="wish",
            idempotency_key="fr179-http-fr",
        )
        code_i, body_i = _post(
            f"{base}/bob/v1/intake",
            issue,
            headers={"X-Bob-Intake-Key": "fleet-secret"},
        )
        code_f, body_f = _post(
            f"{base}/bob/v1/intake",
            fr,
            headers={"X-Bob-Intake-Key": "fleet-secret"},
        )
        assert code_i == code_f == 202
        assert body_i.get("url") and body_f.get("url")
        filed = rx.state.intake_filer.issues
        assert any(i["title"] == "keyed bug" for i in filed)
        assert any(i["title"] == "keyed fr" for i in filed)
        bug = next(i for i in filed if i["title"] == "keyed bug")
        feat = next(i for i in filed if i["title"] == "keyed fr")
        assert "via-intake" in bug["labels"] and "needs-mrb1" in bug["labels"]
        assert "feature-request" in feat["labels"] and "needs-mrb1" in feat["labels"]
        assert "secret-contact" not in bug["body"]
        assert all("secret-contact" not in x for x in rx.state.intake_logs)

        code_bad, body_bad = _post(
            f"{base}/bob/v1/intake",
            build_report_payload(
                kind="issue",
                repo="Nope/nope",
                title="x",
                body="y",
                idempotency_key="fr179-bad-repo",
            ),
            headers={"X-Bob-Intake-Key": "fleet-secret"},
        )
        assert code_bad == 403
        assert body_bad.get("error") == "repo_not_allowed"
    finally:
        rx.stop()


def test_report_skill_lint_no_hardcoded_host():
    assert REPORT_SKILL.is_file()
    text = REPORT_SKILL.read_text(encoding="utf-8")
    assert re.search(r"^name:\s*report\s*$", text, re.M)
    assert "github: https://github.com/SimonBarnett/gh-Jeeves" in text
    assert "{bob-host}" in text
    assert "kind: issue" in text or '"kind": "issue"' in text
    assert "report-outbox" in text
    assert "Invoke-RestMethod" in text
    assert "curl" in text
    # no live host hardcode in skills/report
    assert "irc.ntsa.uk" not in text
    for banned in ("ghp_", "sk-", "BOB_INTAKE_KEY=", "password="):
        assert banned not in text
    # harvest points at report
    harvest = (SKILLS / "harvest" / "SKILL.md").read_text(encoding="utf-8")
    assert "report/SKILL.md" in harvest or "../report/SKILL.md" in harvest
    readme = (SKILLS / "README.md").read_text(encoding="utf-8")
    assert "report" in readme
    assert "#179" in readme or "179" in readme


def test_no_llm_imports_in_intake_module():
    src = (ROOT / "src" / "jeeves" / "intake.py").read_text(encoding="utf-8")
    for banned in ("openai", "anthropic", "requests", "httpx"):
        assert banned not in src
