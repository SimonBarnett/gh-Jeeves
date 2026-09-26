"""FR #20: jeeves-announce-debug skill helpers — dry-run announce path checks.

Overlay only: token-less G1 must not depend on this module or a skill.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import announce as ann
from . import length_safe as ls

SKILL_MD = (
    Path(__file__).resolve().parents[2] / "skills" / "jeeves-announce-debug" / "SKILL.md"
)

REQUIRED_SKILL_HEADINGS = (
    "Commands",
    "Overlay",
    "Trace",
)
REQUIRED_SKILL_MARKERS = (
    "name: jeeves-announce-debug",
    "python -m jeeves.announce_tool",
    "--dry-run",
    "#bobiverse",
    "secret",
)


def skill_complete(path: Path | None = None) -> list[str]:
    path = path or SKILL_MD
    missing: list[str] = []
    if not path.is_file():
        return ["skill file missing"]
    text = path.read_text(encoding="utf-8")
    if "TODO: seed FR" in text:
        missing.append("still stub TODO")
    if not text.strip().startswith("---"):
        missing.append("missing frontmatter")
    for h in REQUIRED_SKILL_HEADINGS:
        if h.lower() not in text.lower():
            missing.append(f"missing:{h}")
    for m in REQUIRED_SKILL_MARKERS:
        if m not in text and m.lower() not in text.lower():
            missing.append(f"missing:{m}")
    if "417" not in text and "length" not in text.lower():
        missing.append("missing:417/length-safe")
    if "outbox" not in text.lower():
        missing.append("missing:outbox")
    if "/bob/v1/git" not in text and "webhook" not in text.lower():
        missing.append("missing:webhook")
    return missing


def _sample_issue_payload(*, title: str, body: str = "", extra: dict | None = None) -> dict:
    p: dict[str, Any] = {
        "action": "opened",
        "issue": {
            "number": 20,
            "title": title,
            "body": body,
            "html_url": "https://github.com/SimonBarnett/gh-Jeeves/issues/20",
        },
        "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
        "sender": {"login": "simon"},
    }
    if extra:
        p.update(extra)
    return p


def run_dry_run(repo_root: Path | None = None) -> dict[str, Any]:
    """Offline announce debug plan: skill lint + sample format + secret filter demos."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    missing = skill_complete(root / "skills" / "jeeves-announce-debug" / "SKILL.md")

    # Normal sample
    ok_payload = _sample_issue_payload(
        title="FR: skill jeeves-announce-debug",
        body="Debug hook deliveries and 417 lines.",
    )
    line, claim, reject = ann.process_git_webhook("issues", ok_payload)
    wire_bytes = ls.wire_line_bytes(line) if line else 0

    # Title mentions ghp_ — must NOT reject (field-only filter / #206)
    mention = _sample_issue_payload(
        title="docs mention ghp_ example token shape",
        body="Do not put real tokens in titles.",
    )
    _l2, _c2, reject_mention = ann.process_git_webhook("issues", mention)

    # Secret in secret-bearing field — must reject
    secret_payload = _sample_issue_payload(title="ok title", body="ok")
    secret_payload["token"] = "ghp_THISISNOTAREALTOKENBUTLOOKSLIKEONE000"
    _l3, _c3, reject_secret = ann.process_git_webhook("issues", secret_payload)

    return {
        "dry_run": True,
        "mutated": False,
        "ok": missing == [],
        "skill_complete": missing,
        "sample_announce": line,
        "sample_claim_task": getattr(claim, "task", None) if claim else None,
        "sample_reject": reject,
        "length_ok": bool(line) and wire_bytes <= ls.IRC_LINE_MAX_BYTES,
        "wire_bytes": wire_bytes,
        "irc_line_max_bytes": ls.IRC_LINE_MAX_BYTES,
        "title_mentions_secret_rejected": reject_mention is not None,
        "secret_field_rejected": reject_secret is not None,
        "secret_field_reason": reject_secret,
        "path": [
            "GitHub hook → POST /bob/v1/git",
            "secret-field filter (not whole payload)",
            "queue update + chair-outbox line",
            "Jeeves drains outbox → #bobiverse only",
        ],
        "commands": [
            "python -m jeeves.announce_tool --dry-run --json",
            "python -m jeeves announce --dry-run --json",
            "Check GitHub webhook Recent Deliveries for /bob/v1/git",
            "jeeves-health if chair-outbox piles up",
        ],
        "forbidden": [
            "announce on shop channels",
            "scan whole payload for secret substrings in title/body",
            "touch Ergo/BobIrcd",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Jeeves announce debug (FR #20): dry-run path checks; never mutates"
    )
    p.add_argument("--dry-run", action="store_true", help="Read-only skill + sample checks")
    p.add_argument("--json", action="store_true", help="JSON output (default with --dry-run)")
    p.add_argument("--repo-root", default="", help="Repo root for skill path")
    args = p.parse_args(argv)

    if not args.dry_run:
        p.error("only --dry-run is supported in this seed")

    root = Path(args.repo_root) if args.repo_root else None
    plan = run_dry_run(root)
    print(json.dumps(plan, indent=2))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
