"""FR #187: UAT is worker-assignable vision-fidelity (not Bob-only stamp)."""

from __future__ import annotations

from pathlib import Path

from jeeves.wire import parse_done

ROOT = Path(__file__).resolve().parents[1]


def test_parse_done_uat_pass_fail():
    p = parse_done(
        "DONE UAT SimonBarnett/gh-Jeeves#187 PASS https://github.com/SimonBarnett/gh-Jeeves/pull/190"
    )
    assert p is not None
    assert p.task == "UAT"
    assert p.repo == "SimonBarnett/gh-Jeeves"
    assert p.number == "187"
    assert (p.result or "").upper().startswith("PASS")

    f = parse_done(
        "DONE UAT SimonBarnett/gh-Jeeves#187 FAIL https://github.com/SimonBarnett/gh-Jeeves/issues/187"
    )
    assert f is not None
    assert f.task == "UAT"
    assert (f.result or "").upper() == "FAIL"


def test_jeeves_uat_skill_exists_and_not_bob_only():
    skill = ROOT / "skills" / "jeeves-uat" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "name: jeeves-uat" in text
    assert "FR #187" in text or "FR #187" in text.replace(" ", "")
    assert "vision" in text.lower()
    low = text.lower()
    assert "not bob-only" in low or "not a human-only" in low or "not bob only" in low
    assert "design-uat" in low


def test_docs_and_skills_drop_only_bob_stamps_uat():
    """Regression: chair docs must not keep the retired Bob-only UAT stamp line."""
    banned = "only bob stamps uat"
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "functional-spec.md",
        ROOT / "docs" / "vision.md",
        ROOT / "docs" / "brief" / "JEEVES_BRIEF.md",
        ROOT / "skills" / "jeeves-task-modes" / "SKILL.md",
        ROOT / "skills" / "jeeves-mrb-gates" / "SKILL.md",
    ]
    hits = []
    for p in paths:
        text = p.read_text(encoding="utf-8").lower()
        if banned in text:
            hits.append(str(p.relative_to(ROOT)))
    assert not hits, hits
