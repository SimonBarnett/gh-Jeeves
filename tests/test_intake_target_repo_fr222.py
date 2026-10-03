"""FR #222: intake files into the payload repo; never default to bobiverse."""
from __future__ import annotations

from pathlib import Path

from jeeves.intake import (
    DEFAULT_ALLOW_REPOS,
    FakeGitHubFiler,
    IntakeConfig,
    process_intake,
    validate_payload,
)


def test_default_allow_repos_includes_fleet_product_repos():
    assert "SimonBarnett/bobiverse" in DEFAULT_ALLOW_REPOS
    assert "SimonBarnett/agentic_fomprep" in DEFAULT_ALLOW_REPOS
    assert "SimonBarnett/gh-Jeeves" in DEFAULT_ALLOW_REPOS
    assert "SimonBarnett/agentic_irc" in DEFAULT_ALLOW_REPOS


def test_missing_repo_is_rejected_not_defaulted_to_bobiverse():
    err, norm = validate_payload({"kind": "issue", "title": "x", "body": "y"})
    assert err == "bad_repo"
    assert norm == {}


def test_empty_repo_is_rejected_not_defaulted_to_bobiverse():
    err, norm = validate_payload(
        {"kind": "issue", "repo": "", "title": "x", "body": "y"}
    )
    assert err == "bad_repo"
    assert norm == {}


def test_agentic_fomprep_issue_filed_in_that_repo_not_bobiverse(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    filer = FakeGitHubFiler()
    res = process_intake(
        home,
        {
            "kind": "issue",
            "repo": "SimonBarnett/agentic_fomprep",
            "title": "catalog BOM note",
            "body": "about fomprep",
            "idempotency_key": "fr222-fomprep-1",
        },
        filer=filer,
    )
    assert res.status == 202
    assert filer.issues
    assert filer.issues[0]["repo"] == "SimonBarnett/agentic_fomprep"
    assert "bobiverse" not in filer.issues[0]["repo"]
    assert "agentic_fomprep" in (res.body.get("url") or "")


def test_payload_repo_is_preserved_exactly(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    filer = FakeGitHubFiler()
    repo = "SimonBarnett/agentic_irc"
    res = process_intake(
        home,
        {
            "kind": "fr",
            "repo": repo,
            "title": "FR: wire",
            "body": "body",
            "idempotency_key": "fr222-irc-1",
        },
        filer=filer,
    )
    assert res.status == 202
    assert filer.issues[0]["repo"] == repo
    assert res.body["url"].startswith(f"https://github.com/{repo}/")


def test_unknown_repo_still_403_not_rewritten(tmp_path: Path):
    home = tmp_path / "h3"
    home.mkdir()
    filer = FakeGitHubFiler()
    res = process_intake(
        home,
        {
            "kind": "issue",
            "repo": "SomeoneElse/not-allowed",
            "title": "nope",
            "body": "x",
            "idempotency_key": "fr222-deny-1",
        },
        filer=filer,
        cfg=IntakeConfig(),
    )
    assert res.status == 403
    assert res.body.get("error") == "repo_not_allowed"
    assert filer.issues == []
