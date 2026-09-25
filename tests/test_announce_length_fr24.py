"""FR #24 acceptance: length-safe announcements (jeeves.announce)."""
from __future__ import annotations

import random
import string
from pathlib import Path

from jeeves import announce as an


def test_long_title_one_line_under_512_vital_present_title_truncated():
    repo = "SimonBarnett/" + ("very-long-repo-name-segment-" * 3) + "end"
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
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    assert parsed.task == "FR"
    assert parsed.ref == vital.ref
    assert parsed.action == "opened"
    assert res.truncated_title or len(parsed.title) < 300
    assert "\n" not in res.line
    assert an.validate_round_trip(res)


def test_multibyte_emoji_never_cut_mid_codepoint():
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
    res.line.encode("utf-8").decode("utf-8")
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    if parsed.title:
        for ch in parsed.title:
            assert ch.encode("utf-8").decode("utf-8") == ch


def test_round_trip_property_random_titles_repos():
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
    res = an.format_announce(vital, line_max=180, nick="J", userhost="u@h", channel="#b")
    parsed = an.parse_announce(res.line)
    assert parsed is not None
    assert parsed.task
    assert parsed.ref


def test_simulated_417_logs_and_queue_still_has_item(tmp_path: Path):
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
    line, res = an.prepare_send(vital, queue_append=q.append, home=tmp_path, log=logs.append)
    assert q.has_ref("SimonBarnett/gh-Jeeves#24")
    assert line is not None
    msg = an.simulate_417(line, log=logs.append, home=tmp_path)
    assert "417" in msg
    assert any("417" in x for x in logs)
    assert q.has_ref("SimonBarnett/gh-Jeeves#24")
    assert list((tmp_path / "queue_events").glob("*.json"))


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


def test_github_payload_mapping_pr_with_fixes():
    payload = {
        "action": "opened",
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


def test_process_git_webhook_public_api():
    line, claim, reject = an.process_git_webhook(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 1, "title": "t" * 400, "html_url": "https://github.com/o/r/issues/1"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "u"},
        },
    )
    assert reject is None
    assert claim is not None
    assert line is not None
    assert line.startswith("GIT issues FR o/r#1")
    assert an.wire_line_bytes(line) <= an.IRC_LINE_MAX_BYTES


def test_token_less_no_network_imports():
    src = Path(an.__file__).read_text(encoding="utf-8")
    for banned in ("openai", "anthropic", "requests", "httpx", "socket"):
        assert banned not in src
