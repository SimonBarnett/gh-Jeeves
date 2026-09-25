"""CAST IRON policy helpers (FR #106: Jeeves assigns on !bored).

Scripts only. Jeeves hears !bored in #{machine} and posts one assign line.
Legacy ear OFFER / git-claim / ASSIGN paths remain forbidden.
"""

from __future__ import annotations

import re
from pathlib import Path

from .assign import is_assign_egress

# Forbidden chair behaviours: legacy ear claim grammar + multi-wake ASSIGN.
_FORBIDDEN_SHOP = re.compile(
    r"(?i)\b("
    r"OFFER\b"
    r"|ASSIGN\b"
    r"|NAK\s+!?\s*BORED"
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
    """FR #106: Jeeves owns !bored → assign."""
    return True


def chair_may_offer() -> bool:
    """FR #106: chair may post assign lines (not legacy OFFER keyword)."""
    return True


def chair_may_post_claim_in_shop() -> bool:
    """Assign lines in shop are allowed; legacy claim grammar is not."""
    return True


def is_forbidden_shop_egress(text: str) -> bool:
    """True if a shop PRIVMSG body is legacy claim/OFFER (chair must not send)."""
    t = (text or "").strip()
    if not t:
        return False
    # FR #106 assign / empty-queue lines are allowed
    if is_assign_egress(t):
        return False
    if _FORBIDDEN_SHOP.search(t):
        return True
    # bare "repo FR #n" style legacy claim lines (no nick: prefix)
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\s+(FR|MRB|UAT|PR)\s+#?\d+", t, re.I):
        return True
    if re.search(r"(?i)\bno\s+jobs\b", t) and not t.lower().endswith("nothing queued"):
        return True
    return False


def shop_egress_allowed_for_chair(text: str) -> bool:
    """Chair may PRIVMSG shops only for FR #106 assign / nothing-queued lines."""
    return is_assign_egress(text)


def scan_source_for_forbidden_chair_handlers(root: Path | None = None) -> list[str]:
    """Static guard: chair must not keep legacy !bored claim / OFFER path."""
    root = Path(root) if root else Path(__file__).resolve().parent
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if path.name in ("cast_iron.py", "assign.py"):
            continue
        if path.name in ("roles.py",):
            text = path.read_text(encoding="utf-8")
            if "class JeevesChair" in text and "class BobEar" in text:
                chair_part = text.split("class BobEar")[0]
            else:
                chair_part = text
            for sym in FORBIDDEN_CHAIR_SYMBOLS:
                if sym in chair_part:
                    hits.append(f"{path.name}:{sym}")
            # Legacy OFFER keyword from chair is forbidden; assign.py is the path.
            if re.search(r"class JeevesChair[\s\S]*?format_single_line_offer\s*\(", chair_part):
                hits.append(f"{path.name}:format_single_line_offer_in_chair")
            if re.search(r"class JeevesChair[\s\S]*?\bclaim_top\s*\(", chair_part):
                hits.append(f"{path.name}:claim_top_in_chair")
        elif path.name.endswith(".py"):
            text = path.read_text(encoding="utf-8")
            if path.name in ("wire.py", "queue.py", "resync.py", "announce.py", "offer.py"):
                continue
            for sym in ("_git_bored", "_maybe_git_claim"):
                if sym in text:
                    hits.append(f"{path.name}:{sym}")
    return hits
