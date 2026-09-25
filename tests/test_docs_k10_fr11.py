"""FR #11 / K10: docs must say Jeeves assigns (not offers).

Seed outcome: fix docs and skills; gh-Jeeves README is the source of truth
with small diagrams. agentic_build README diagrams 2–4 and the git-accept /
token-handoff skills must not say "Jeeves offers the (top) job".

Failing-test-first: encode the SoT + forbid the old phrasing before the
sibling docs are corrected.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GH_JEEVES_README = ROOT / "README.md"
AGENTIC_BUILD = ROOT.parent / "agentic_build"
AGENTIC_README = AGENTIC_BUILD / "README.md"

# Phrases retired by FR #106 / K10 (case-insensitive).
FORBIDDEN_OFFER = re.compile(
    r"jeeves\s+offers(\s+the)?(\s+top)?\s+job",
    re.IGNORECASE,
)
# Mermaid / caption variants used in agentic_build README diagrams 2–4.
FORBIDDEN_OFFER_TOP = re.compile(
    r"jeeves\s+offers\s+top\s+job",
    re.IGNORECASE,
)

# Skill paths named in brief §0.1 K10 / §16.2 phase 5 (renames allowed).
SKILL_CANDIDATES = (
    AGENTIC_BUILD / ".grok" / "skills" / "bob-token-handoff" / "SKILL.md",
    AGENTIC_BUILD / ".grok" / "skills" / "bob-token-efficient-handoff" / "SKILL.md",
    AGENTIC_BUILD / ".grok" / "skills" / "bob-git-accept" / "SKILL.md",
    AGENTIC_BUILD / ".grok" / "skills" / "bob-git-accept-claim" / "SKILL.md",
    AGENTIC_BUILD / ".grok" / "skills" / "bob-jeeves-chair" / "SKILL.md",
)


def _offer_hits(text: str) -> list[str]:
    hits: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if FORBIDDEN_OFFER.search(line) or FORBIDDEN_OFFER_TOP.search(line):
            hits.append(f"L{i}: {line.strip()}")
    return hits


def test_gh_jeeves_readme_is_sot_assigns_with_diagram():
    """gh-Jeeves README is SoT: assigns on !bored, small mermaid chain."""
    text = GH_JEEVES_README.read_text(encoding="utf-8")
    assert "Jeeves assigns" in text
    assert "Jeeves owns `!bored` → assign" in text or "Jeeves owns `!bored`" in text
    assert "```mermaid" in text
    assert not _offer_hits(text), (
        "gh-Jeeves README must not say Jeeves offers the job:\n"
        + "\n".join(_offer_hits(text))
    )


@pytest.mark.skipif(not AGENTIC_README.is_file(), reason="agentic_build checkout not beside gh-Jeeves")
def test_agentic_build_readme_diagrams_say_assigns_not_offers():
    """Diagrams 2–4 (idle !bored / chair / FR→MRB→UAT) must match FR #106."""
    text = AGENTIC_README.read_text(encoding="utf-8")
    hits = _offer_hits(text)
    assert hits == [], (
        "agentic_build README still says Jeeves offers (K10); "
        "align with gh-Jeeves README (Jeeves assigns):\n" + "\n".join(hits)
    )
    # Positive: idle/bored path documents assign.
    assert re.search(r"jeeves\s+assigns", text, re.IGNORECASE), (
        "agentic_build README must say Jeeves assigns (SoT: gh-Jeeves README)"
    )


@pytest.mark.skipif(not AGENTIC_BUILD.is_dir(), reason="agentic_build checkout not beside gh-Jeeves")
def test_agentic_build_skills_do_not_say_jeeves_offers():
    """bob-jeeves-chair / bob-git-accept / bob-token-handoff must not offer-phrase."""
    present = [p for p in SKILL_CANDIDATES if p.is_file()]
    assert present, "expected at least one of the K10-named skills under agentic_build/.grok/skills"
    bad: list[str] = []
    for path in present:
        hits = _offer_hits(path.read_text(encoding="utf-8"))
        for h in hits:
            bad.append(f"{path.relative_to(AGENTIC_BUILD)}: {h}")
    assert bad == [], "skills still say Jeeves offers:\n" + "\n".join(bad)
