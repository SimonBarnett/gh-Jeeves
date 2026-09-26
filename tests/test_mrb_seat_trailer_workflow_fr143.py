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


def test_live_workflow_if_guarded_must_be_balanced():
    """Catch the #145 footgun: || '' with only one closing brace."""
    if not WF.is_file():
        return
    expr = _body_expr(WF.read_text(encoding="utf-8"))
    if "||" in expr:
        assert expr.endswith("}}"), (
            "unguarded close: push parse fails with zero jobs — "
            f"use tools/mrb-seat-trailer.yml.desired / Apply-MrbSeatTrailerWorkflowFix.ps1 -Write: {expr!r}"
        )
