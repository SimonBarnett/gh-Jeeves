"""FR #146: brief + functional-spec must match README SoT (Jeeves assigns on !bored).

Stale claims that Jeeves never handles !bored / never assigns are forbidden.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "docs" / "brief" / "JEEVES_BRIEF.md"
SPEC = ROOT / "docs" / "functional-spec.md"
VISION = ROOT / "docs" / "vision.md"
README = ROOT / "README.md"

# Active (non-historical) docs must not restate the pre-#106 CAST IRON.
FORBIDDEN = re.compile(
    r"never\s+handles?\s+`?!bored`?"
    r"|never\s+handles?\s+!bored"
    r"|never\s+offer(s|ing)?\s+or\s+assign"
    r"|never\s+!bored/offers"
    r"|silent;\s*never\s+!bored",
    re.IGNORECASE,
)


def _hits(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    out: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if FORBIDDEN.search(line):
            out.append(f"{path.name}:L{i}: {line.strip()}")
    return out


def test_readme_sot_assigns():
    text = README.read_text(encoding="utf-8")
    assert "Jeeves owns `!bored` → assign" in text or "Jeeves owns `!bored`" in text
    assert "Jeeves assigns" in text


def test_brief_section1_and_cast_iron_assign():
    text = BRIEF.read_text(encoding="utf-8")
    assert "Assign-on-`!bored` (FR #106)" in text or "FR #106" in text
    assert "Jeeves **owns**" in text or "Jeeves assigns" in text or "**assigns** the next job" in text
    # §1 must not list "handles !bored" under Jeeves never
    never_block = re.search(
        r"\*\*Jeeves never:\*\*(.*?)(?:\n- Jeeves is|\n---|\n## )",
        text,
        re.S,
    )
    assert never_block, "expected Jeeves never: block in brief §1"
    assert "!bored" not in never_block.group(1) or "emits ear-style" in never_block.group(1)
    assert not re.search(r"handles\s+`?!bored", never_block.group(1), re.I)


def test_functional_spec_and_vision_forbid_stale_never_bored():
    bad = _hits(SPEC) + _hits(VISION) + _hits(BRIEF)
    # Allow historical K1 resolution lines that mention the old rule in past tense only via "Resolved"
    bad = [h for h in bad if "Resolved by FR #106" not in h and "historical" not in h.lower()]
    assert bad == [], "stale never-handles-!bored claims remain:\n" + "\n".join(bad)


def test_functional_spec_says_assign():
    text = SPEC.read_text(encoding="utf-8")
    assert "Jeeves owns `!bored` → assign" in text or "Jeeves assign" in text
    assert "ear `OFFER` retired" in text or "ear OFFER" in text.lower() or "OFFER` retired" in text
