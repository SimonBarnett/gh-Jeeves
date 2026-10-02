"""Machine-capability routing for shop assign (bobiverse#168).

Chair / ircJeeves / chair-outbox jobs need the ionos seat (where the chair
runs). Marchhare and other boxes get ``nothing queued`` for those rows until
an ionos worker !boreds.
"""

from __future__ import annotations

import re
from typing import Any

from .digest import normalize_machine_id
from .nicks import parse_worker_nick

# Fleet chair host (ircJeeves + chair-outbox).
CHAIR_HOST_MACHINE = "ionos"

_CHAIR_LABELS = frozenset(
    {
        "needs-ionos",
        "chair-host",
        "capability:chair",
        "capability-chair",
        "capability/chair",
    }
)

# Title/body cues that the fix lives on the chair box.
_CHAIR_TEXT_RX = re.compile(
    r"(?i)\b("
    r"chair-outbox|chair\s+outbox|ircjeeves|"
    r"chanserv(?:\s|-)?list|chair[- ]?status|"
    r"jeeves\s+drains|drains\s+chair|"
    r"git\s*announce.*chair|chair.*git\s*announce"
    r")\b"
)


def _label_names(labels: Any) -> set[str]:
    out: set[str] = set()
    if not labels:
        return out
    for lab in labels:
        if isinstance(lab, dict):
            name = str(lab.get("name") or "").strip().lower()
        else:
            name = str(lab or "").strip().lower()
        if name:
            out.add(name)
    return out


def infer_require_machine(
    *,
    title: str = "",
    body: str = "",
    labels: Any = None,
    explicit: str = "",
) -> str | None:
    """Return a required machine id, or None if any fleet seat may take the job."""
    exp = normalize_machine_id(str(explicit or "").strip()) or str(explicit or "").strip().lower()
    if exp:
        return exp
    names = _label_names(labels)
    if names & _CHAIR_LABELS:
        return CHAIR_HOST_MACHINE
    blob = f"{title or ''}\n{body or ''}"
    if _CHAIR_TEXT_RX.search(blob):
        return CHAIR_HOST_MACHINE
    return None


def machine_of_nick(nick: str) -> str | None:
    parsed = parse_worker_nick(nick)
    if not parsed:
        return None
    return parsed[0].lower()


def require_machine_of_row(row: dict[str, Any]) -> str | None:
    """Effective require_machine for a queue row (explicit field or inferred)."""
    if not isinstance(row, dict):
        return None
    explicit = str(row.get("require_machine") or row.get("required_machine") or "").strip()
    return infer_require_machine(
        title=str(row.get("line") or ""),
        body=str(row.get("body") or ""),
        labels=row.get("labels") or [],
        explicit=explicit,
    )


def row_blocked_for_machine(row: dict[str, Any], nick: str) -> bool:
    """True when this seat's machine cannot take the row (bobiverse#168)."""
    req = require_machine_of_row(row)
    if not req:
        return False
    mine = machine_of_nick(nick)
    if not mine:
        return True
    return mine != req.lower()
