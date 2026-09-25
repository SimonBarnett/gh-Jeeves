"""FR #27: !help by PM, registry-driven, no flood."""

from __future__ import annotations

import time
from pathlib import Path

from jeeves.commands import (
    COMMANDS,
    commands_for_roles,
    readme_command_table,
    validate_registry,
)
from jeeves.helpcmd import (
    HELP_DETAIL_MAX_LINES,
    HELP_LINE_MAX,
    HelpRateLimit,
    build_help,
    format_index_line,
    parse_help,
    roles_for_nick,
    utf8_len,
)
from jeeves.length_safe import utf8_len as _u
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def test_registry_every_command_has_syntax_and_summary():
    assert validate_registry() == []
    assert len(COMMANDS) >= 4
    for c in COMMANDS:
        assert c.syntax.strip()
        assert c.summary.strip()
        assert utf8_len(format_index_line(c)) <= HELP_LINE_MAX


def test_worker_help_hides_resync():
    lines = build_help("flamingo-46804", include_shop_pointer=False).lines
    joined = "\n".join(lines)
    assert "!help" in joined or "help" in joined.lower()
    assert "!list" in joined
    assert "!status" in joined
    assert not any(ln.strip().lower().startswith("!resync") for ln in lines)
    assert "!resync" not in joined


def test_simon_help_includes_restricted():
    lines = build_help("simon", include_shop_pointer=False).lines
    assert any("resync" in ln.lower() for ln in lines)
    bob_lines = build_help("bob-flamingo", include_shop_pointer=False).lines
    assert any("resync" in ln.lower() for ln in bob_lines)


def test_help_list_detail_at_most_5_lines_with_example():
    r = build_help("flamingo-1", "list", include_shop_pointer=False)
    assert len(r.lines) <= HELP_DETAIL_MAX_LINES
    assert len(r.lines) >= 3
    assert any("example:" in ln.lower() for ln in r.lines)
    assert any("syntax:" in ln.lower() for ln in r.lines)


def test_help_unknown_one_line():
    r = build_help("flamingo-1", "nosuch")
    assert len(r.lines) == 1
    assert "unknown command" in r.lines[0].lower()
    assert "try !help" in r.lines[0].lower()


def test_help_rate_limit_30s():
    rate = HelpRateLimit(interval_s=30.0)
    now = 1_000_000.0
    a = build_help("flamingo-1", rate=rate, now=now, include_shop_pointer=False)
    assert not a.rate_limited
    b = build_help("flamingo-1", rate=rate, now=now + 5, include_shop_pointer=False)
    assert b.rate_limited
    assert "rate limit" in b.lines[0].lower()
    c = build_help("flamingo-1", rate=rate, now=now + 31, include_shop_pointer=False)
    assert not c.rate_limited


def test_parse_help():
    assert parse_help("!help") == (True, None)
    assert parse_help("!HELP list") == (True, "list")
    assert parse_help("!list") == (False, None)


def test_roles_for_nick():
    assert "worker" in roles_for_nick("flamingo-1")
    assert "bob" in roles_for_nick("bob-ionos")
    assert "simon" in roles_for_nick("simon")
    vis_w = {c.name for c in commands_for_roles(roles_for_nick("flamingo-1"))}
    assert "resync" not in vis_w
    vis_s = {c.name for c in commands_for_roles(roles_for_nick("simon"))}
    assert "resync" in vis_s


def test_readme_table_generated():
    t = readme_command_table()
    assert "| `!help`" in t
    assert "resync" in t


def test_help_pm_not_channel(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    j = JeevesChair("127.0.0.1", port, home, f"http://127.0.0.1:{rport}", shops=["#flamingo"])
    worker = IrcClient("127.0.0.1", port, "flamingo-27")
    worker.join("#flamingo")
    sniffer = IrcClient("127.0.0.1", port, "sniff-help")
    sniffer.join("#flamingo")
    j.start()
    time.sleep(0.15)
    try:
        # !help in channel → PMs only
        worker.privmsg("#flamingo", "!help")
        deadline = time.time() + 5
        while time.time() < deadline and not j.pm_egress:
            time.sleep(0.05)
        assert j.pm_egress, "expected PM lines"
        assert all(n == "flamingo-27" for n, _ in j.pm_egress)
        # no Jeeves channel help flood
        time.sleep(0.3)
        sniffer.wait_privmsg(timeout=0.2)
        jeeves_ch = [
            m
            for m in sniffer.inbox
            if m[0].lower() == "jeeves" and m[1].lower() == "#flamingo" and "help" in m[2].lower()
        ]
        assert jeeves_ch == []
        assert any(h.startswith("help:flamingo-27") for h in j.handled)
        # each line under 400 bytes
        for _, line in j.pm_egress:
            assert _u(line) <= HELP_LINE_MAX
    finally:
        j.stop()
        worker.close()
        sniffer.close()
        rx.stop()
        ircd.stop()
