"""FR #24: length-safe announcements — vital fields first; title only truncated."""
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
    """AC1: 300-char title + long repo → one body line; vitals parse; title cut."""
    repo = "SimonBarnett/very-long-repository-name-for-fleet-testing-gh-jeeves"
    title = "A" * 300
    url = f"https://github.com/{repo}/issues/24"
    fmt = an.format_git_announce(
        event="issues",
        repo=repo,
        number=24,
        action="opened",
        url=url,
        title=title,
        task="FR",
        irc_max=512,
    )
    assert fmt.ok
    assert an.utf8_len(fmt.body) <= an.body_budget(irc_max=512)
    wire = an.utf8_len(fmt.body) + an.irc_prefix_bytes()
    assert wire <= 512
    parsed = an.parse_git_announce(fmt.body)
    assert parsed is not None
    assert parsed.repo == repo
    assert parsed.number == "24"
    assert parsed.task == "FR"
    assert parsed.action == "opened"
    assert parsed.event == "issues"
    assert url in fmt.body or fmt.compact
    if not fmt.compact:
        assert parsed.title != title  # truncated
        assert an.utf8_len(parsed.title) < an.utf8_len(title)
        assert "..." in parsed.title or len(parsed.title) < 300
    assert an.validate_round_trip(fmt)


def test_multibyte_emoji_never_cut_mid_codepoint():
    """AC2: emoji/multibyte titles never split mid-codepoint."""
    snow = "\u2603"  # 3-byte
    emoji = "\U0001f680"  # 4-byte rocket
    title = (snow + emoji) * 80
    fmt = an.format_git_announce(
        event="issues",
        repo="SimonBarnett/gh-Jeeves",
        number=1,
        action="opened",
        url="https://github.com/SimonBarnett/gh-Jeeves/issues/1",
        title=title,
        irc_max=512,
    )
    assert fmt.ok
    # body must decode as valid UTF-8 (always true for str) and re-encode stable
    body_b = fmt.body.encode("utf-8")
    assert body_b.decode("utf-8") == fmt.body
    parsed = an.parse_git_announce(fmt.body)
    assert parsed is not None
    if parsed.title:
        # every char in title is a complete codepoint (no lone surrogates)
        parsed.title.encode("utf-8").decode("utf-8")
        for ch in parsed.title:
            assert ch.encode("utf-8").decode("utf-8") == ch


def test_round_trip_property_random_titles_repos():
    """AC3: parse(format(event)).vital_core matches for random titles/repos."""
    rng = random.Random(24)
    for _ in range(40):
        owner = "Org" + "".join(rng.choice(string.ascii_letters) for _ in range(rng.randint(3, 12)))
        name = "repo-" + "".join(rng.choice(string.ascii_lowercase + "-") for _ in range(rng.randint(5, 40)))
        repo = f"{owner}/{name}"
        title = "".join(rng.choice(string.ascii_letters + " .-" + "\u00e9\u2603") for _ in range(rng.randint(0, 200)))
        num = rng.randint(1, 9999)
        fmt = an.format_git_announce(
            event="issues",
            repo=repo,
            number=num,
            action="opened",
            url=f"https://github.com/{repo}/issues/{num}",
            title=title,
            head="",
            irc_max=512,
        )
        assert fmt.ok, fmt
        assert an.validate_round_trip(fmt), fmt.body


def test_vital_overflow_emits_compact_and_parses():
    """AC4: vital fields over budget → compact form still parses."""
    # Force tiny budget so even base vitals need compact
    repo = "SimonBarnett/x"
    fmt = an.format_git_announce(
        event="pull_request",
        repo=repo,
        number=334,
        action="opened",
        url="https://github.com/SimonBarnett/x/pull/334",
        title="ignored",
        task="MRB",
        fixes_repo="SimonBarnett/x",
        fixes_number=24,
        head="fix/very-long-branch-name-that-eats-budget",
        budget=60,
    )
    assert fmt.ok
    assert fmt.compact or an.utf8_len(fmt.body) <= 60
    parsed = an.parse_git_announce(fmt.body)
    assert parsed is not None
    assert parsed.repo == repo
    assert parsed.number == "334"
    assert parsed.task == "MRB"
    assert an.validate_round_trip(fmt)


def test_simulated_417_logs_and_queue_event_exists(tmp_path: Path):
    """AC5: simulated 417 → error logged; queue item from payload still written."""
    logs: list[str] = []
    vital = an.VitalFields(
        kind="announce",
        event="issues",
        task="FR",
        repo="SimonBarnett/gh-Jeeves",
        number="24",
        action="opened",
        url="https://github.com/SimonBarnett/gh-Jeeves/issues/24",
    )
    qdir = tmp_path / "queue_events"
    path = an.handle_simulated_417(
        "GIT " + ("x" * 600),
        queue_dir=qdir,
        vital=vital,
        payload_hint={"delivery_id": "d-1", "event": "issues"},
        log=logs.append,
    )
    assert path.is_file()
    assert any("417" in x for x in logs)
    data = path.read_text(encoding="utf-8")
    assert "irc_417" in data
    assert "SimonBarnett/gh-Jeeves" in data
    assert "d-1" in data


def test_prepare_send_rejects_bad_round_trip_writes_queue(tmp_path: Path):
    bad = an.FormatResult(
        body="GIT not-a-real-line",
        vital=an.VitalFields(kind="announce", task="FR", repo="a/b", number="1"),
        ok=True,
    )
    logs: list[str] = []
    body, qpath = an.prepare_send(bad, queue_dir=tmp_path / "q", log=logs.append)
    assert body is None
    assert qpath is not None and qpath.is_file()
    assert any("ERROR" in x for x in logs)


def test_offer_ack_done_list_single_line_vitals():
    off = an.format_offer(
        "marchhare-34992",
        "FR",
        "SimonBarnett/gh-Jeeves",
        24,
        "https://github.com/SimonBarnett/gh-Jeeves/issues/24",
    )
    assert off.ok and an.validate_round_trip(off)
    assert "OFFER FR SimonBarnett/gh-Jeeves#24" in off.body
    assert "\n" not in off.body

    ack = an.format_ack("FR", "SimonBarnett/gh-Jeeves", 24)
    assert an.validate_round_trip(ack)

    done = an.format_done(
        "FR",
        "SimonBarnett/gh-Jeeves",
        24,
        "PASS",
        "https://github.com/SimonBarnett/gh-Jeeves/pull/1",
    )
    assert an.validate_round_trip(done)

    row = an.format_list_row(1, "FR", "SimonBarnett/gh-Jeeves", 24, "5m", "A" * 400)
    assert an.utf8_len(row.body) <= 350
    assert an.validate_round_trip(row)


def test_title_sanitised_no_crlf_or_secretish():
    t = an.sanitize_title("hello\r\nworld sk-abcdefghijklmnopqrst ghp_abcdefghijklmnopqrstuv")
    assert "\n" not in t and "\r" not in t
    assert "sk-" not in t.lower() or "[redacted]" in t
    assert "[redacted]" in t


def test_from_github_payload_pr_fixes_and_head():
    payload = {
        "action": "opened",
        "repository": {"full_name": "SimonBarnett/agentic_build"},
        "pull_request": {
            "number": 334,
            "title": "fix tsr",
            "html_url": "https://github.com/SimonBarnett/agentic_build/pull/334",
            "body": "Fixes SimonBarnett/agentic_build#24",
            "merged": False,
            "head": {"ref": "fix/irc-tsr"},
        },
    }
    fmt = an.format_from_github_payload("pull_request", payload)
    assert fmt.ok
    assert an.validate_round_trip(fmt)
    p = an.parse_git_announce(fmt.body)
    assert p is not None
    assert p.task == "MRB"
    assert p.head == "fix/irc-tsr"
    assert p.fixes_repo == "SimonBarnett/agentic_build"
    assert p.fixes_number == "24"


def test_budget_accounts_for_prefix_and_crlf():
    # body budget + prefix + CRLF == irc_max
    pref = an.irc_prefix_bytes()
    bud = an.body_budget(irc_max=512)
    assert pref + bud == 512
