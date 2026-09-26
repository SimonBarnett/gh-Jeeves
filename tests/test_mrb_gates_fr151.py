"""FR #151: two MRB gates documented; intake tags needs-mrb1."""
from __future__ import annotations

from pathlib import Path

from jeeves.intake import _labels_for

ROOT = Path(__file__).resolve().parents[1]


def test_mrb_gates_doc_teaches_two_gates():
    text = (ROOT / "docs" / "mrb-gates.md").read_text(encoding="utf-8")
    assert "MRB #1" in text and "MRB #2" in text
    assert "needs-mrb1" in text
    assert "mrb1-pass" in text
    assert "mrb1-reject" in text
    assert "vision" in text.lower() or "fit" in text.lower()
    assert "implementation" in text.lower() or "fidelity" in text.lower()
    assert "Simon" in text
    # Do not tell seats to stamp MRB #1
    assert "seats do **not** stamp fit" in text or "seats do not stamp fit" in text.lower()


def test_mrb_gates_skill_exists():
    skill = ROOT / "skills" / "jeeves-mrb-gates" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "name: jeeves-mrb-gates" in text
    assert "MRB #1" in text and "MRB #2" in text
    assert "needs-mrb1" in text
    assert "Do not conflate" in text or "not a code review" in text.lower()


def test_skills_readme_lists_mrb_gates():
    text = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-mrb-gates" in text
    assert "#151" in text


def test_intake_fr_and_issue_get_needs_mrb1():
    fr = _labels_for({"kind": "fr"}, quarantine=False)
    assert "needs-mrb1" in fr
    assert "feature-request" in fr
    issue = _labels_for({"kind": "issue"}, quarantine=False)
    assert "needs-mrb1" in issue
    skill = _labels_for({"kind": "skill"}, quarantine=False)
    assert "needs-mrb1" not in skill


def test_mrb_enforcement_points_at_gates_doc():
    text = (ROOT / "docs" / "mrb-enforcement.md").read_text(encoding="utf-8")
    assert "mrb-gates.md" in text
    assert "MRB #2" in text or "FR #151" in text


def test_mrb_gates_doc_requires_label_ensure():
    text = (ROOT / "docs" / "mrb-gates.md").read_text(encoding="utf-8")
    assert "ensure_mrb1_labels" in text
    assert "422" in text or "must exist" in text.lower()


def test_ensure_mrb1_labels_tool_lists_three_labels():
    tool = (ROOT / "tools" / "ensure_mrb1_labels.py").read_text(encoding="utf-8")
    assert "needs-mrb1" in tool
    assert "mrb1-pass" in tool
    assert "mrb1-reject" in tool
    assert "LABELS" in tool
