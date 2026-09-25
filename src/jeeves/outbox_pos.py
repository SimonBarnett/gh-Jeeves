"""FR #71: chair-outbox read position — migrate legacy name, never replay on cutover.

Canonical pos file: ``chair-outbox.pos`` (byte offset into ``chair-outbox.txt``).
Legacy agentic_irc used ``chair-outbox.txt.pos``. Starting gh-Jeeves against an
existing digest home without migration would re-announce the whole outbox.
"""

from __future__ import annotations

from pathlib import Path

OUTBOX_NAME = "chair-outbox.txt"
POS_NAME = "chair-outbox.pos"
LEGACY_POS_NAME = "chair-outbox.txt.pos"


def outbox_path(home: Path) -> Path:
    return Path(home) / OUTBOX_NAME


def pos_path(home: Path) -> Path:
    return Path(home) / POS_NAME


def legacy_pos_path(home: Path) -> Path:
    return Path(home) / LEGACY_POS_NAME


def _read_pos_file(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip() or "0"
        return int(raw)
    except (OSError, ValueError):
        return None


def write_pos(home: Path, offset: int) -> None:
    """Persist canonical pos (never log outbox contents)."""
    p = pos_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(max(0, int(offset))), encoding="utf-8")


def resolve_outbox_start(
    home: Path,
    *,
    outbox_size: int | None = None,
    replay: bool = False,
) -> int:
    """
    Byte offset to start draining ``chair-outbox.txt``.

    1. If ``chair-outbox.pos`` exists → use it.
    2. Else if legacy ``chair-outbox.txt.pos`` exists → migrate value into
       ``chair-outbox.pos`` and use it.
    3. Else if outbox is non-empty and not ``replay`` → start at EOF (no flood).
    4. Else → 0 (empty outbox, or explicit ``--replay-outbox``).

    When offset is past EOF, clamp to EOF (do not reset to 0 / replay).
    """
    home = Path(home)
    size = outbox_size
    if size is None:
        ob = outbox_path(home)
        size = ob.stat().st_size if ob.is_file() else 0
    size = max(0, int(size))

    canonical = pos_path(home)
    legacy = legacy_pos_path(home)

    pos: int | None = _read_pos_file(canonical)
    if pos is None:
        legacy_val = _read_pos_file(legacy)
        if legacy_val is not None:
            pos = legacy_val
            write_pos(home, pos)
        elif size > 0 and not replay:
            # Cutover: existing outbox, no pos → park at EOF (no #bobiverse flood)
            pos = size
            write_pos(home, pos)
        else:
            # Empty outbox, or explicit replay: start at 0 and persist so later
            # appends are drained (not treated as another cutover).
            pos = 0
            write_pos(home, 0)

    if pos < 0:
        pos = 0
    if pos > size:
        pos = size
        write_pos(home, pos)
    return pos
