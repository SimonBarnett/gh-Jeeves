"""FR #175 / K1: chair handles !bored via FR #106 assign path (blocks gate closed)."""
from __future__ import annotations

from pathlib import Path

from jeeves.cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    scan_source_for_forbidden_chair_handlers,
    shop_egress_allowed_for_chair,
)
from jeeves.assign import is_assign_egress


def test_k1_chair_handles_bored_is_true():
    """Brief K1 seed outcome (FR #106): Jeeves owns !bored -> assign."""
    assert chair_handles_bored() is True
    assert chair_may_offer() is True  # assign lines, not legacy OFFER keyword


def test_k1_assign_egress_allowed_not_legacy_offer():
    assert is_assign_egress("marchhare-9460: FR SimonBarnett/gh-Jeeves#175 https://example/x")
    assert shop_egress_allowed_for_chair(
        "marchhare-9460: FR SimonBarnett/gh-Jeeves#175 https://example/x"
    )
    assert shop_egress_allowed_for_chair("marchhare-9460: nothing queued")
    assert not shop_egress_allowed_for_chair("OFFER SimonBarnett/gh-Jeeves#1 https://x")


def test_k1_no_legacy_chair_bored_claim_handlers():
    hits = scan_source_for_forbidden_chair_handlers(Path(__file__).resolve().parents[1] / "src" / "jeeves")
    assert hits == [], hits


def test_k1_brief_marks_resolved_by_fr106():
    brief = (Path(__file__).resolve().parents[1] / "docs" / "brief" / "JEEVES_BRIEF.md").read_text(
        encoding="utf-8"
    )
    assert "K1" in brief
    assert "FR #106" in brief
    # Must not claim ear owns !bored as current SoT for K1
    k1_line = [ln for ln in brief.splitlines() if ln.startswith("| K1 |")][0]
    assert "FR #106" in k1_line
    assert "assign" in k1_line.lower()
