"""FR #19: jeeves-queue skill helpers — inspect / dry-run (never silent mutate).

Overlay only: the token-less G1 path must not depend on this module or a skill.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SKILL_MD = Path(__file__).resolve().parents[2] / "skills" / "jeeves-queue" / "SKILL.md"

REQUIRED_SKILL_HEADINGS = (
    "Purpose",
    "Commands",
    "Overlay",
    "Supersede",
)
REQUIRED_SKILL_MARKERS = (
    "name: jeeves-queue",
    "python -m jeeves.queue_tool",
    "--dry-run",
    "!list",
    "resync",
    "queue.json",
)


def skill_complete(path: Path | None = None) -> list[str]:
    """Return missing requirements for skills/jeeves-queue/SKILL.md."""
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
    if "supersede" not in text.lower():
        missing.append("missing:supersede")
    if "dry-run" not in text.lower() and "--dry-run" not in text:
        missing.append("missing:dry-run")
    if "!ignore" not in text and "ignored.json" not in text:
        missing.append("missing:ignore")
    if "focus" not in text.lower():
        missing.append("missing:focus")
    return missing


_SUPERSEDE_RULES = {
    "issue opened/reopened": "enqueue FR",
    "PR opened (Closes #n)": "drop FR #n; enqueue MRB",
    "PR merged (MRB PASS)": "drop MRB; enqueue UAT",
    "PR closed unmerged": "drop MRB; restore FR if issue open",
    "MRB FAIL (DONE … FAIL)": "restore FR; mrb_fail_hold (K15)",
    "issue closed": "drop FR/UAT (unless mrb_fail_hold)",
}


def run_dry_run(
    repo_root: Path | None = None,
    *,
    digest_home: Path | None = None,
) -> dict[str, Any]:
    """Offline queue plan: skill lint + read-only snapshot. Never writes queue.json."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    missing = skill_complete(root / "skills" / "jeeves-queue" / "SKILL.md")

    home = digest_home
    if home is None:
        env = os.environ.get("BOB_DIGEST_HOME") or os.environ.get("JEEVES_DIGEST_HOME") or ""
        home = Path(env) if env else None

    from .queue import load_queue, queue_counts

    counts = {"unaccepted": 0, "accepted": 0, "done": 0, "workers": 0}
    sample: list[dict[str, Any]] = []
    hold: list[str] = []
    path_note = "digest home unset — empty snapshot"
    if home is not None and Path(home).is_dir():
        doc = load_queue(Path(home))
        counts = queue_counts(Path(home))
        counts["workers"] = len(doc.get("workers") or {})
        sample = list(doc.get("unaccepted") or [])[:10]
        hold = list(doc.get("mrb_fail_hold") or [])
        path_note = str(Path(home) / "queue.json")

    return {
        "dry_run": True,
        "mutated": False,
        "ok": missing == [],
        "skill_complete": missing,
        "digest_home": str(home) if home else "",
        "queue_path": path_note,
        "counts": counts,
        "unaccepted_sample": [
            {
                "repo": r.get("repo"),
                "task": r.get("task"),
                "id": r.get("id"),
                "line": (r.get("line") or "")[:80],
            }
            for r in sample
            if isinstance(r, dict)
        ],
        "mrb_fail_hold": hold,
        "rules": dict(_SUPERSEDE_RULES),
        "commands": [
            "python -m jeeves.queue_tool --dry-run --json",
            "python -m jeeves queue --dry-run --json",
            "IRC: !list (PM reply)",
            "IRC: !resync (ops)",
            "IRC: !ignore / !unignore / !ignored",
            "IRC: !focus / !unfocus (simon)",
        ],
        "forbidden": [
            "edit queue.json without dry-run first",
            "touch Ergo/BobIrcd",
            "depend on LLM for supersede",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Jeeves queue tool (FR #19): dry-run inspect; never silent mutate"
    )
    p.add_argument("--dry-run", action="store_true", help="Read-only plan + skill lint")
    p.add_argument("--json", action="store_true", help="JSON output (default with --dry-run)")
    p.add_argument(
        "--digest-home",
        default="",
        help="Queue home (default BOB_DIGEST_HOME)",
    )
    p.add_argument("--repo-root", default="", help="Repo root for skill path")
    args = p.parse_args(argv)

    if not args.dry_run:
        p.error("only --dry-run is supported in this seed (manual apply stays operator IRC/tools)")

    root = Path(args.repo_root) if args.repo_root else None
    home = Path(args.digest_home) if args.digest_home else None
    plan = run_dry_run(root, digest_home=home)
    print(json.dumps(plan, indent=2))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
