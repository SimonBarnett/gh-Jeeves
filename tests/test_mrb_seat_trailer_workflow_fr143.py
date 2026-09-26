"""MRB #145 / #143: mrb-seat-trailer BODY must use balanced ${{ }} with || '' guard."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "mrb-seat-trailer.yml"
DESIRED = ROOT / "tools" / "mrb-seat-trailer.yml.desired"


def _body_expr(text: str) -> str:
    m = re.search(r"^\s*BODY:\s*(.+)$", text, re.M)
    assert m, "BODY env line missing"
    return m.group(1).strip()


def test_desired_workflow_is_balanced_and_guarded():
    text = DESIRED.read_text(encoding="utf-8")
    expr = _body_expr(text)
    assert expr.startswith("${{")
    assert expr.endswith("}}"), f"unbalanced: {expr!r}"
    assert "pull_request.body" in expr and "||" in expr
    assert re.search(r"(?ms)^on:\s*.*?^\s*push:\s*$.*?branches:\s*\[main\]", text)


def test_live_workflow_matches_desired_when_present():
    """If .github copy is readable in CI checkout, it must match the desired fix."""
    if not WF.is_file():
        return
    live = WF.read_text(encoding="utf-8")
    desired = DESIRED.read_text(encoding="utf-8")
    # Compare BODY lines and push trigger presence
    assert _body_expr(live) == _body_expr(desired)
    assert "||" in _body_expr(live)
    assert _body_expr(live).endswith("}}")
