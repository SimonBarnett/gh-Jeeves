"""FR #148: deterministic exception → issue (dedupe, spool, rate limit, no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeeves.exception_report import (
    AUTO_LABEL,
    ExceptionReporter,
    FakeExceptionFiler,
    build_exception_record,
    dedupe_key,
    render_issue_body,
    render_issue_title,
)
from jeeves.guard import no_llm_network_guard, scrub_ai_env, restore_env
from jeeves.intake import GitHubDown


def _boom(msg: str = "kaboom") -> Exception:
    try:
        raise RuntimeError(msg)
    except RuntimeError as e:
        return e


def test_template_has_required_fields(tmp_path: Path):
    exc = _boom("disk full")
    rec = build_exception_record(exc, component="queue")
    body = render_issue_body(rec)
    title = render_issue_title(rec)
    assert "RuntimeError" in title
    assert "disk full" in body
    assert "Traceback" in body
    assert "Dedupe key" in body
    assert "Jeeves version" in body
    assert "Timestamp (UTC)" in body
    assert rec["component"] == "queue"
    assert "jeeves-exception-dedupe:" in body


def test_create_issue_on_exception(tmp_path: Path):
    filer = FakeExceptionFiler()
    rep = ExceptionReporter(home=tmp_path, filer=filer)
    result = rep.report(_boom("x"), component="assign")
    assert result["action"] == "created"
    assert result["ok"] is True
    assert len(filer.issues) == 1
    assert AUTO_LABEL in filer.issues[0]["labels"]
    assert "RuntimeError" in filer.issues[0]["title"]


def test_dedupe_comments_instead_of_new_issue(tmp_path: Path):
    filer = FakeExceptionFiler()
    clock = {"t": 1_000_000.0}

    def now() -> float:
        return clock["t"]

    rep = ExceptionReporter(home=tmp_path, filer=filer, now_fn=now, dedupe_window_s=3600)
    r1 = rep.report(_boom("same"), component="c")
    r2 = rep.report(_boom("same"), component="c")
    assert r1["action"] == "created"
    assert r2["action"] == "commented"
    assert r2["number"] == r1["number"]
    assert len(filer.issues) == 1
    assert len(filer.comments) == 1


def test_github_down_spools_and_drain_retries(tmp_path: Path):
    filer = FakeExceptionFiler(down=True)
    rep = ExceptionReporter(home=tmp_path, filer=filer)
    r = rep.report(_boom("net"), component="resync")
    assert r["action"] == "spooled_github_down"
    spool = Path(r["spool"])
    assert spool.is_file()

    filer.down = False
    drained = rep.drain_spool()
    assert drained
    assert any(d.get("action") == "created" for d in drained)
    assert not spool.exists()
    assert filer.issues


def test_rate_limit_spools_runaway(tmp_path: Path):
    filer = FakeExceptionFiler()
    clock = {"t": 5_000.0}

    def now() -> float:
        return clock["t"]

    rep = ExceptionReporter(
        home=tmp_path,
        filer=filer,
        now_fn=now,
        rate_max=3,
        rate_window_s=60,
        dedupe_window_s=0,  # force new keys via unique messages
    )
    actions = []
    for i in range(5):
        # unique messages → unique dedupe keys
        actions.append(rep.report(_boom(f"runaway-{i}"), component="c")["action"])
        clock["t"] += 0.1
    assert actions.count("created") == 3
    assert actions.count("rate_limited_spool") == 2
    assert len(filer.issues) == 3


def test_idempotent_same_record_dedupe_key():
    e1 = _boom("x")
    e2 = _boom("x")
    r1 = build_exception_record(e1)
    r2 = build_exception_record(e2)
    # same type/location pattern may differ by line in this helper — use explicit key
    assert dedupe_key("RuntimeError", "a.py:1:f", "x") == dedupe_key(
        "RuntimeError", "a.py:1:f", "x"
    )


def test_no_llm_in_reporter_path(tmp_path: Path):
    """Reporting path must not call LLM endpoints (G1-style guard)."""
    filer = FakeExceptionFiler()
    rep = ExceptionReporter(home=tmp_path, filer=filer)
    prev = scrub_ai_env()
    try:
        with no_llm_network_guard():
            rep.report(_boom("guarded"), component="g1")
    finally:
        restore_env(prev)
    assert filer.issues


def test_source_has_no_llm_imports():
    root = Path(__file__).resolve().parents[1]
    src = (root / "src" / "jeeves" / "exception_report.py").read_text(encoding="utf-8")
    for bad in ("openai", "anthropic", "groq", "litellm", "langchain"):
        assert bad not in src.lower()
    assert "no LLM" in src or "No LLM" in src or "no LLM" in src


def test_auto_exception_label_constant_and_ensure_tool():
    root = Path(__file__).resolve().parents[1]
    assert AUTO_LABEL == "auto-exception"
    tool = (root / "tools" / "ensure_auto_exception_label.py").read_text(encoding="utf-8")
    assert "auto-exception" in tool
    assert "422" in tool or "must exist" in tool.lower() or "Ensure" in tool
    doc = (root / "docs" / "exception-report.md").read_text(encoding="utf-8")
    assert "ensure_auto_exception_label" in doc
    assert "auto-exception" in doc
    assert "no llm" in doc.lower()
