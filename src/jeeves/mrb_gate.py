"""FR #92: no-self-merge + real MRB verdict gate (shared GitHub identity seats).

Because every fleet seat pushes as the same GitHub user (SimonBarnett), GitHub
"different author" reviews cannot tell seats apart. Enforcement is a required
status/check ``mrb/verdict`` whose payload records:

- author seat nick (from PR body ``Seat:`` trailer)
- reviewer seat nick (the seat posting the verdict)
- full pytest command + exit code
- review wall-clock duration

The check fails if reviewer == author, if the suite did not pass, if the command
is not a full ``tests/`` run, or if duration is below the minimum (default 10 min).
Simon may still merge with admin override on branch protection.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

CHECK_NAME = "mrb/verdict"
DEFAULT_MIN_DURATION_S = 600  # 10 minutes
SEAT_TRAILER = re.compile(
    r"(?im)^\s*Seat:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$"
)
# Full-suite command must mention pytest and tests/ (or tests\)
FULL_SUITE = re.compile(
    r"(?i)pytest.*(?:\btests\b|tests[/\\]|\btests/\b)",
)


@dataclass
class MrbVerdict:
    """Payload posted as the mrb/verdict check output."""

    pr: int
    head_sha: str
    author_seat: str
    reviewer_seat: str
    verdict: str  # PASS | FAIL
    pytest_cmd: str
    pytest_exit: int
    duration_s: float
    notes: str = ""
    min_duration_s: int = DEFAULT_MIN_DURATION_S

    def errors(self) -> list[str]:
        return validate_verdict(self)


def parse_seat_trailer(text: str | None) -> str | None:
    """Return Seat: nick from PR body (first match), or None."""
    if not text:
        return None
    m = SEAT_TRAILER.search(text)
    if not m:
        return None
    return m.group(1).strip()


def normalize_seat(nick: str | None) -> str:
    return (nick or "").strip().lower()


def is_full_suite_cmd(cmd: str | None) -> bool:
    """True only for whole-tree suite runs (``tests/`` or ``tests`` as the target)."""
    c = (cmd or "").strip()
    if not c:
        return False
    low = c.lower()
    if "pytest" not in low:
        return False
    if "::" in c:
        return False
    # any path under tests/ that is more specific than the suite root
    if re.search(r"tests[/\\].+", low):
        # allow only exact tests/ or tests/\s flags after
        if not re.search(r"tests[/\\](?:\s|$)", low):
            return False
    # bare tests directory target
    return bool(re.search(r"(?:^|\s)tests(?:/|\\)?(?:\s|$)", low))


def validate_verdict(v: MrbVerdict) -> list[str]:
    """Return human-readable errors; empty list means the check may pass."""
    errs: list[str] = []
    author = normalize_seat(v.author_seat)
    reviewer = normalize_seat(v.reviewer_seat)
    if not author:
        errs.append("missing author_seat (PR body needs 'Seat: {nick}')")
    if not reviewer:
        errs.append("missing reviewer_seat")
    if author and reviewer and author == reviewer:
        errs.append(
            f"self-MRB forbidden: reviewer_seat={v.reviewer_seat!r} equals author_seat={v.author_seat!r}"
        )
    verd = (v.verdict or "").strip().upper()
    if verd not in ("PASS", "FAIL"):
        errs.append("verdict must be PASS or FAIL")
    if not is_full_suite_cmd(v.pytest_cmd):
        errs.append(
            "pytest_cmd must be a full-suite run (e.g. 'python -m pytest tests/ -q'), not scoped-only"
        )
    try:
        code = int(v.pytest_exit)
    except (TypeError, ValueError):
        errs.append("pytest_exit must be an int")
        code = -1
    if verd == "PASS" and code != 0:
        errs.append(f"PASS requires pytest_exit=0 (got {code})")
    try:
        dur = float(v.duration_s)
    except (TypeError, ValueError):
        errs.append("duration_s must be a number")
        dur = 0.0
    min_d = int(v.min_duration_s or DEFAULT_MIN_DURATION_S)
    if dur < min_d:
        errs.append(
            f"review too short: duration_s={dur:.0f} < min_duration_s={min_d} "
            f"(hostile MRB floor; Simon may admin-override merge)"
        )
    if not (v.head_sha or "").strip():
        errs.append("missing head_sha")
    if int(v.pr or 0) <= 0:
        errs.append("missing pr number")
    return errs


def verdict_to_check_output(v: MrbVerdict, errors: list[str]) -> dict[str, Any]:
    """GitHub Check Run output title/summary/text."""
    ok = not errors
    title = f"MRB {v.verdict.upper()} by {v.reviewer_seat}" if ok else "MRB gate failed"
    lines = [
        f"author_seat: {v.author_seat}",
        f"reviewer_seat: {v.reviewer_seat}",
        f"verdict: {v.verdict}",
        f"pytest_cmd: {v.pytest_cmd}",
        f"pytest_exit: {v.pytest_exit}",
        f"duration_s: {v.duration_s}",
        f"pr: #{v.pr}",
        f"head_sha: {v.head_sha}",
    ]
    if v.notes:
        lines.append(f"notes: {v.notes}")
    if errors:
        lines.append("errors:")
        lines.extend(f"  - {e}" for e in errors)
    summary = (
        "mrb/verdict OK — required check may pass"
        if ok
        else "mrb/verdict FAILED — fix self-MRB / suite / duration"
    )
    return {
        "title": title[:1024],
        "summary": summary[:1024],
        "text": "\n".join(lines)[:60000],
    }


def build_check_run_payload(v: MrbVerdict) -> dict[str, Any]:
    """Body for POST /repos/{o}/{r}/check-runs."""
    conclusion, errors = finalize_check_conclusion(v)
    out = verdict_to_check_output(v, errors)
    if conclusion == "neutral":
        out["summary"] = "MRB FAIL recorded (valid process; do not merge as PASS)"
    return {
        "name": CHECK_NAME,
        "head_sha": v.head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": out,
    }


def finalize_check_conclusion(v: MrbVerdict) -> tuple[str, list[str]]:
    """
    Return (conclusion, errors).

    - validation errors → failure
    - PASS + valid → success (merge allowed when check required)
    - FAIL + valid → neutral (recorded; not a green merge signal)
    """
    errs = validate_verdict(v)
    if errs:
        return "failure", errs
    if (v.verdict or "").upper() == "PASS":
        return "success", []
    return "neutral", []


def seat_trailer_line(nick: str) -> str:
    return f"Seat: {nick.strip()}"
