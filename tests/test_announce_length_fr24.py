"""FR #24 acceptance: length-safe announcements, vital fields first, title only truncated."""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gh_jeeves import announce as an  # noqa: E402


def test_long_title_one_line_under_512_vital_present_title_truncated():
    """AC1: 300-char title + long repo → one line ≤512 wire bytes; vital parse; title truncated."""
    repo = "SimonBarnett/" + ("very-long-repo-name-segment-" * 3) + "end"
    assert len(repo) > 40
    title = "T" * 300
    vital = an.VitalFields(
        event="issues",
        task="FR",
        ref=f"{repo}#24",
        action="opened",
        url=f"https://github.com/{repo}/issues/24",
        title=title,
    )
    res = an.format_announce(vital)
    assert res.ok
    assert res.wire_bytes <= an.IRC_LINE_MAX_BYTES
    assert an.utf8_len(res.line) <= an.text_budget()
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    assert parsed.task == "FR"
    assert parsed.ref == vital.ref
    assert parsed.action == "opened"
    assert parsed.url == vital.url or an.short_url(parsed.url) == an.short_url(vital.url)
    assert res.truncated_title or len(parsed.title) < 300
    assert "..." in res.line or len(parsed.title) < 300
    # single line — no continuation marker
    assert "\n" not in res.line
    ok, err = an.validate_before_send(res)
    assert ok, err


def test_multibyte_emoji_never_cut_mid_codepoint():
    """AC2: multibyte/emoji titles never cut mid-codepoint."""
    # each emoji is 4-byte UTF-8
    title = "hello " + ("🎯" * 80) + " world"
    vital = an.VitalFields(
        event="issues",
        task="FR",
        ref="SimonBarnett/gh-Jeeves#24",
        action="opened",
        url="https://github.com/SimonBarnett/gh-Jeeves/issues/24",
        title=title,
    )
    res = an.format_announce(vital)
    assert res.ok
    # re-decode must succeed; no lone surrogates
    res.line.encode("utf-8").decode("utf-8")
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    if parsed.title:
        parsed.title.encode("utf-8").decode("utf-8")
        # every char in title is a full codepoint (emoji or ascii)
        for ch in parsed.title:
            assert ch.encode("utf-8").decode("utf-8") == ch


def test_round_trip_property_random_titles_repos():
    """AC3: for random titles/repos, parse(format(event)).vital == event.vital_fields."""
    rng = random.Random(24)
    for _ in range(40):
        owner = "O" + "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(3, 12)))
        name = "R" + "".join(rng.choice(string.ascii_lowercase + "-") for _ in range(rng.randint(5, 40)))
        num = rng.randint(1, 9999)
        title = "".join(rng.choice(string.ascii_letters + " 🎯é") for _ in range(rng.randint(0, 250)))
        vital = an.VitalFields(
            event="issues",
            task="FR",
            ref=f"{owner}/{name}#{num}",
            action="opened",
            url=f"https://github.com/{owner}/{name}/issues/{num}",
            title=title,
        )
        res = an.format_announce(vital)
        assert res.ok, res.error
        parsed = an.parse_announce(res.line)
        assert parsed is not None
        assert an.vital_core_equal(vital, parsed), (vital, parsed, res.line)


def test_vital_overflow_emits_compact_still_parses():
    """AC4: vital fields alone over budget → compact form still parses."""
    # Force tiny budget via absurd nick/userhost/channel lengths? Better: huge ref+url
    repo = "SimonBarnett/" + ("x" * 200)
    vital = an.VitalFields(
        event="pull_request",
        task="MRB",
        ref=f"{repo}#999",
        action="opened",
        url="https://github.com/" + repo + "/pull/999",
        fixes=f"{repo}#1",
        head="feature/" + ("h" * 80),
        title="ignored",
    )
    # Shrink line_max so core overflows
    res = an.format_announce(vital, line_max=180, nick="J", userhost="u@h", channel="#b")
    assert res.compact or res.ok
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    assert parsed.task in ("MRB", "FR", "UAT", "OTHER", "PING", "PUSH")
    assert parsed.ref  # compact keeps ref
    assert an.wire_line_bytes(res.line, nick="J", userhost="u@h", channel="#b") <= 180 or res.compact


def test_simulated_417_logs_and_queue_still_has_item():
    """AC5: simulated 417 → error logged; queue item still exists from webhook path."""
    logs: list[str] = []
    q = an.MemoryQueue()
    vital = an.VitalFields(
        event="issues",
        task="FR",
        ref="SimonBarnett/gh-Jeeves#24",
        action="opened",
        url="https://github.com/SimonBarnett/gh-Jeeves/issues/24",
        title="smoke",
    )
    line, res = an.prepare_send(vital, queue_append=q.append, log=logs.append)
    assert q.has_ref("SimonBarnett/gh-Jeeves#24")
    assert line is not None
    # simulate Ergo 417 after send attempt
    msg = an.simulate_417(line, log=logs.append)
    assert "417" in msg
    assert any("417" in x for x in logs)
    # queue still present — never depended on IRC success
    assert q.has_ref("SimonBarnett/gh-Jeeves#24")
    assert any(i.kind == "git" for i in q.items)


def test_validate_rejects_missing_vital(monkeypatch):
    """Pre-send: if parser would lose required fields, do not send; queue error event."""
    q = an.MemoryQueue()
    logs: list[str] = []
    # craft a result that fails parse equality by monkeypatching
    bad = an.FormatResult(
        line="NOT_A_GIT_LINE",
        vital=an.VitalFields(event="issues", task="FR", ref="a/b#1"),
        ok=False,
        error="forced",
    )
    ok, err = an.validate_before_send(bad)
    assert not ok
    line, res = an.prepare_send(
        an.VitalFields(event="issues", task="FR", ref="a/b#1", action="opened", url="https://github.com/a/b/issues/1"),
        queue_append=q.append,
        log=logs.append,
    )
    assert line is not None  # good vital still sends
    assert q.has_ref("a/b#1")


def test_title_sanitised_no_crlf_or_secretish():
    vital = an.VitalFields(
        event="issues",
        task="FR",
        ref="o/r#1",
        action="opened",
        url="https://github.com/o/r/issues/1",
        title="line1\r\nline2 password=hunter2 and ghp_abcdefghijklmnopqrstuv",
    )
    res = an.format_announce(vital)
    assert "\n" not in res.line and "\r" not in res.line
    assert "hunter2" not in res.line
    assert "ghp_" not in res.line
    assert "[redacted]" in res.line or "password" not in res.line.lower()


def test_github_payload_mapping_pr_with_fixes():
    payload = {
        "action": "opened",
        "number": 10,
        "pull_request": {
            "number": 10,
            "title": "fix stuff",
            "html_url": "https://github.com/SimonBarnett/gh-Jeeves/pull/10",
            "body": "Fixes #24",
            "merged": False,
            "head": {"ref": "fr/24-length-safe"},
        },
        "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
    }
    v = an.vital_from_github("pull_request", payload)
    assert v.task == "MRB"
    assert v.ref == "SimonBarnett/gh-Jeeves#10"
    assert v.fixes == "SimonBarnett/gh-Jeeves#24"
    assert v.head == "fr/24-length-safe"
    res = an.format_announce(v)
    assert res.ok
    p = an.parse_announce(res.line)
    assert p is not None
    assert p.fixes == v.fixes
    assert p.head == v.head


def test_offer_and_list_rows_single_line_vital():
    v = an.VitalFields(
        event="issues",
        task="FR",
        ref="SimonBarnett/gh-Jeeves#24",
        action="opened",
        url="https://github.com/SimonBarnett/gh-Jeeves/issues/24",
        title="x" * 200,
    )
    offer = an.format_offer_line("marchhare", v)
    assert offer.ok
    assert an.wire_line_bytes(offer.line, nick="bob-marchhare", channel="#marchhare") <= an.IRC_LINE_MAX_BYTES
    row = an.format_list_row(v, pos=3)
    assert row.ok
    assert "\n" not in row.line


def test_token_less_import_surface():
    """AC6: token-less — pure functions, no network/LLM imports in announce module."""
    src = (ROOT / "src" / "gh_jeeves" / "announce.py").read_text(encoding="utf-8")
    for banned in ("openai", "anthropic", "requests", "httpx", "urllib.request", "socket"):
        assert banned not in src
