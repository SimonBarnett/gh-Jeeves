"""FR #135: DONE parser must accept >2 trailing words; extract PASS/FAIL + URL."""
from __future__ import annotations

import logging

from jeeves.wire import looks_like_done, parse_done


def test_done_with_fail_fix_url_accepted():
    """Real 26 Sep line that was silently dropped."""
    line = (
        "DONE MRB SimonBarnett/AgentMonitor#106 FAIL fix#108 "
        "https://github.com/SimonBarnett/AgentMonitor/pull/108"
    )
    d = parse_done(line)
    assert d is not None
    assert d.task == "MRB"
    assert d.repo == "SimonBarnett/AgentMonitor"
    assert d.number == "106"
    assert d.result.upper() == "FAIL"
    assert d.url == "https://github.com/SimonBarnett/AgentMonitor/pull/108"


def test_done_many_trailing_words_still_parses():
    d = parse_done(
        "DONE FR SimonBarnett/gh-Jeeves#135 ok merged both "
        "https://github.com/SimonBarnett/gh-Jeeves/pull/999 extra note"
    )
    assert d is not None
    assert d.number == "135"
    assert "https://github.com/SimonBarnett/gh-Jeeves/pull/999" in d.url
    assert d.result  # non-empty


def test_done_pass_nits_and_bare_ok():
    p = parse_done("DONE FR SimonBarnett/gh-Jeeves#4 PASS https://x")
    assert p is not None and p.result.upper().startswith("PASS") and p.url == "https://x"
    o = parse_done("DONE FR SimonBarnett/gh-Jeeves#4 ok https://y")
    assert o is not None and o.result == "ok" and o.url == "https://y"
    bare = parse_done("DONE UAT SimonBarnett/AgentMonitor#106")
    assert bare is not None and bare.result == "ok" and bare.url == ""


def test_done_unparsed_prefix_without_full_match():
    assert looks_like_done("DONE something broken")
    assert parse_done("DONE something broken") is None
    assert looks_like_done(
        "DONE MRB SimonBarnett/AgentMonitor#106 FAIL fix#108 "
        "https://github.com/SimonBarnett/AgentMonitor/pull/108"
    )


def test_chair_logs_done_unparsed(tmp_path, caplog):
    """Chair must warn done_unparsed instead of silent drop (FR #135)."""
    from jeeves.roles import JeevesChair

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    home = tmp_path / "chair"
    home.mkdir()
    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        shops=["#marchhare"],
        client=FakeClient(),
    )
    with caplog.at_level(logging.WARNING, logger="jeeves.chair"):
        chair._handle_shop("marchhare-31712", "#marchhare", "DONE nonsense no job id")
    assert any("done_unparsed" in r.getMessage() for r in caplog.records)
    assert any(h.startswith("done_unparsed:") for h in chair.handled)
