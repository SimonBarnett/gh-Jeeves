"""Config lint helpers (FR #10 / K9: no duplicate JSON keys in bobiverse.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CANONICAL_REPORT_URL = "https://irc.ntsa.uk/bob/v1/report"
LEGACY_REPORT_URL_7700 = "https://irc.ntsa.uk:7700/bob/v1/report"


def find_duplicate_json_keys(text: str) -> list[str]:
    """Return keys that appear more than once at any object level (order preserved)."""
    dups: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in seen and k not in dups:
                dups.append(k)
            seen.add(k)
            out[k] = v
        return out

    json.loads(text, object_pairs_hook=hook)
    return dups


def lint_bobiverse_config(path: Path | str) -> list[str]:
    """Lint a bobiverse.json-shaped file. Returns human-readable issue strings."""
    p = Path(path)
    issues: list[str] = []
    if not p.is_file():
        return [f"missing:{p}"]
    text = p.read_text(encoding="utf-8")
    try:
        dups = find_duplicate_json_keys(text)
    except json.JSONDecodeError as exc:
        return [f"invalid_json:{exc}"]
    for k in dups:
        issues.append(f"duplicate_key:{k}")
    # Prefer counting raw keys for reportUrl (K9 evidence)
    if text.count('"reportUrl"') > 1:
        if "duplicate_key:reportUrl" not in issues:
            issues.append("duplicate_key:reportUrl")
    if LEGACY_REPORT_URL_7700 in text and text.count('"reportUrl"') == 1:
        # Single key still pointing at :7700 — warn (canonical is IIS HTTPS without port)
        if CANONICAL_REPORT_URL not in text:
            issues.append(f"legacy_reportUrl_port_7700:use:{CANONICAL_REPORT_URL}")
    return issues
