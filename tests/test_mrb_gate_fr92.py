"""FR #92: mrb/verdict gate validation."""

from __future__ import annotations

from jeeves.mrb_gate import (
    CHECK_NAME,
    MrbVerdict,
    finalize_check_conclusion,
    is_full_suite_cmd,
    parse_seat_trailer,
    seat_trailer_line,
    validate_verdict,
)


def test_parse_seat_trailer():
    body = "Fixes #1\n\nSeat: flamingo-43052\n\nMore text\n"
    assert parse_seat_trailer(body) == "flamingo-43052"
    assert parse_seat_trailer("no trailer") is None
    assert seat_trailer_line("ionos-1") == "Seat: ionos-1"


def test_full_suite_cmd_detection():
    assert is_full_suite_cmd("python -m pytest tests/ -q")
    assert is_full_suite_cmd("pytest -q tests/")
    assert is_full_suite_cmd("python -m pytest -q tests")
    assert not is_full_suite_cmd("pytest -q tests/test_focus_fr68.py")
    assert not is_full_suite_cmd("pytest -q tests/g1_token_less_e2e")
    assert not is_full_suite_cmd("")


def test_self_mrb_rejected():
    v = MrbVerdict(
        pr=1,
        head_sha="abc",
        author_seat="flamingo-43052",
        reviewer_seat="flamingo-43052",
        verdict="PASS",
        pytest_cmd="python -m pytest tests/ -q",
        pytest_exit=0,
        duration_s=700,
    )
    errs = validate_verdict(v)
    assert any("self-MRB" in e for e in errs)
    conc, _ = finalize_check_conclusion(v)
    assert conc == "failure"


def test_short_review_rejected():
    v = MrbVerdict(
        pr=1,
        head_sha="abc",
        author_seat="flamingo-1",
        reviewer_seat="ionos-2",
        verdict="PASS",
        pytest_cmd="python -m pytest tests/ -q",
        pytest_exit=0,
        duration_s=35,
    )
    errs = validate_verdict(v)
    assert any("too short" in e for e in errs)


def test_pass_requires_exit_zero():
    v = MrbVerdict(
        pr=1,
        head_sha="abc",
        author_seat="a-1",
        reviewer_seat="b-2",
        verdict="PASS",
        pytest_cmd="python -m pytest tests/ -q",
        pytest_exit=1,
        duration_s=700,
    )
    assert any("pytest_exit" in e for e in validate_verdict(v))


def test_pass_ok():
    v = MrbVerdict(
        pr=90,
        head_sha="deadbeef",
        author_seat="ionos-9504",
        reviewer_seat="flamingo-43052",
        verdict="PASS",
        pytest_cmd="python -m pytest tests/ -q",
        pytest_exit=0,
        duration_s=720,
    )
    assert validate_verdict(v) == []
    conc, errs = finalize_check_conclusion(v)
    assert conc == "success" and errs == []


def test_fail_valid_is_neutral():
    v = MrbVerdict(
        pr=90,
        head_sha="deadbeef",
        author_seat="ionos-9504",
        reviewer_seat="flamingo-43052",
        verdict="FAIL",
        pytest_cmd="python -m pytest tests/ -q",
        pytest_exit=1,
        duration_s=800,
    )
    assert validate_verdict(v) == []
    conc, _ = finalize_check_conclusion(v)
    assert conc == "neutral"


def test_check_name_constant():
    assert CHECK_NAME == "mrb/verdict"
