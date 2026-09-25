"""CAST IRON policy helpers (K1: Jeeves never !bored / never offers).

Scripts only. The bob-{machine} ear owns !bored → OFFER; Jeeves is silent in
shops except recording ACK/DONE (no shop posts for claims).
"""

from __future__ import annotations

import re
from pathlib import Path

# Forbidden chair behaviours in #{machine} shop channels.
_OFFER_OR_CLAIM = re.compile(
    r"(?i)\b("
    r"OFFER\b"
    r"|ASSIGN\b"
    r"|NAK\s+!?\s*BORED"
    r"|no\s+jobs\b"
    r"|claim_top"
    r"|git-claim"
    r"|_git_bored"
    r")",
)

# Legacy agentic_irc chair symbols that must not appear in gh-Jeeves chair path.
FORBIDDEN_CHAIR_SYMBOLS = (
    "_git_bored",
    "_maybe_git_claim",
    "claim_top_http",
    "op=git-claim",
    "git-claim",
)


def chair_handles_bored() -> bool:
    """K1 invariant: always False in gh-Jeeves."""
    return False


def chair_may_offer() -> bool:
    return False


def chair_may_post_claim_in_shop() -> bool:
    return False


def is_forbidden_shop_egress(text: str) -> bool:
    """True if a shop PRIVMSG body would be a claim/offer (chair must not send)."""
    t = (text or "").strip()
    if not t:
        return False
    if _OFFER_OR_CLAIM.search(t):
        return True
    # bare "repo FR #n" style legacy claim lines
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\s+(FR|MRB|UAT|PR)\s+#?\d+", t, re.I):
        return True
    return False


def shop_egress_allowed_for_chair(text: str) -> bool:
    """Chair must not PRIVMSG shop channels at all for normal ops (silent listener)."""
    return False


def scan_source_for_forbidden_chair_handlers(root: Path | None = None) -> list[str]:
    """Static guard: roles.py / chair modules must not define legacy !bored claim path."""
    root = Path(root) if root else Path(__file__).resolve().parent
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if path.name in ("cast_iron.py",):
            continue
        # ear is allowed to offer
        if path.name in ("roles.py",):
            text = path.read_text(encoding="utf-8")
            # JeevesChair class body must not call offer/claim helpers for bored
            # Split roughly on class BobEar
            if "class JeevesChair" in text and "class BobEar" in text:
                chair_part = text.split("class BobEar")[0]
            else:
                chair_part = text
            for sym in FORBIDDEN_CHAIR_SYMBOLS:
                if sym in chair_part:
                    hits.append(f"{path.name}:{sym}")
            # chair must not format_offer or top_unaccepted for bored path
            if "format_offer(" in chair_part and "JeevesChair" in chair_part:
                # only illegal if used inside chair methods — format_offer import at module ok if unused in chair
                if re.search(r"class JeevesChair[\s\S]*?format_offer\s*\(", chair_part):
                    hits.append(f"{path.name}:format_offer_in_chair")
            if re.search(r"class JeevesChair[\s\S]*?top_unaccepted\s*\(", chair_part):
                hits.append(f"{path.name}:top_unaccepted_in_chair")
            if re.search(r"class JeevesChair[\s\S]*?\bclaim_top\s*\(", chair_part):
                hits.append(f"{path.name}:claim_top_in_chair")
        elif path.name.endswith(".py"):
            text = path.read_text(encoding="utf-8")
            if path.name in ("wire.py", "queue.py", "resync.py", "announce.py"):
                continue
            for sym in ("_git_bored", "_maybe_git_claim"):
                if sym in text:
                    hits.append(f"{path.name}:{sym}")
    return hits
