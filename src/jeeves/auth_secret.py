"""X-Bob-Secret loading and check (FR #47). Never log the secret value."""

from __future__ import annotations

import os
from pathlib import Path


def load_bob_secret(
    *,
    env: dict | None = None,
    homes: list[Path] | None = None,
) -> str:
    """
    Resolve shared write secret.

    Order: BOB_CALLBACK_SECRET, X_BOB_SECRET, BOB_SECRET,
    then first existing file among BOB_CALLBACK_SECRET_FILE and
    {home}/bob.secret / {home}/.bob-secret.
    """
    e = env if env is not None else os.environ
    for key in ("BOB_CALLBACK_SECRET", "X_BOB_SECRET", "BOB_SECRET"):
        raw = (e.get(key) or "").strip()
        if raw:
            return raw
    paths: list[Path] = []
    fe = (e.get("BOB_CALLBACK_SECRET_FILE") or "").strip()
    if fe:
        paths.append(Path(fe).expanduser())
    for h in homes or []:
        if h:
            paths.append(Path(h).expanduser() / "bob.secret")
            paths.append(Path(h).expanduser() / ".bob-secret")
    paths.append(Path.home() / ".grok" / "bob.secret")
    for p in paths:
        try:
            if p.is_file():
                tok = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                if tok:
                    return tok
        except OSError:
            continue
    return ""


def check_bob_secret(headers: dict[str, str] | None, expected: str) -> bool:
    """True if X-Bob-Secret matches expected (both non-empty and equal)."""
    if not expected:
        return False
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    got = (hdrs.get("x-bob-secret") or "").strip()
    return bool(got) and got == expected
