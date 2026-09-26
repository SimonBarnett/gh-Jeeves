"""FR #21: jeeves-worker-state skill helpers — dry-run ACK/DONE busy path.

Overlay only: token-less G1 must not depend on this module or a skill.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SKILL_MD = (
    Path(__file__).resolve().parents[2] / "skills" / "jeeves-worker-state" / "SKILL.md"
)

REQUIRED_SKILL_HEADINGS = (
    "Commands",
    "Overlay",
    "State",
)
REQUIRED_SKILL_MARKERS = (
    "name: jeeves-worker-state",
    "python -m jeeves.worker_state_tool",
    "--dry-run",
    "ACK",
    "DONE",
    "working_on",
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
    if "busy" not in text.lower() or "idle" not in text.lower():
        missing.append("missing:busy/idle")
    if "TipForm" not in text and "working_on" not in text:
        missing.append("missing:TipForm/working_on")
    if "echo" not in text.lower() and "K12" not in text and "hidden" not in text.lower():
        missing.append("missing:K12/echo/hidden")
    return missing


def _simulate_ack_done(home: Path) -> dict[str, Any]:
    """Demo K12/K3 path in an isolated home (never the live digest unless caller passes temp)."""
    from .digest import apply_report, load_digest, public_digest_snapshot

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    nick = "marchhare-31712"
    job = "SimonBarnett/gh-Jeeves FR #21"
    apply_report(
        home,
        {"op": "worker_state", "nick": nick, "state": "busy", "job": job},
    )
    snap_busy = public_digest_snapshot(home)
    m_busy = (snap_busy.get("machines") or {}).get("marchhare") or {}
    after_ack = {
        "busy": m_busy.get("workers", {}).get(nick, {}).get("state") == "busy"
        or (load_digest(home).get("workers") or {}).get(nick, {}).get("state") == "busy",
        "working_on": m_busy.get("working_on") or "",
        "nick": nick,
        "job": job,
    }
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "idle", "job": None})
    snap_idle = public_digest_snapshot(home)
    m_idle = (snap_idle.get("machines") or {}).get("marchhare") or {}
    after_done = {
        "busy": False,
        "working_on": m_idle.get("working_on") or "",
        "state": (m_idle.get("workers") or {}).get(nick, {}).get("state"),
    }
    return {"after_ack": after_ack, "after_done": after_done}


def run_dry_run(
    repo_root: Path | None = None,
    *,
    digest_home: Path | None = None,
) -> dict[str, Any]:
    """Offline worker-state plan: skill lint + isolated ACK/DONE TipForm demo."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    missing = skill_complete(root / "skills" / "jeeves-worker-state" / "SKILL.md")

    live_note = "no live digest snapshot"
    live_counts: dict[str, Any] = {}
    home_env = digest_home
    if home_env is None:
        env = os.environ.get("BOB_DIGEST_HOME") or ""
        home_env = Path(env) if env else None
    if home_env is not None and Path(home_env).is_dir():
        try:
            from .queue import load_queue, queue_counts

            live_counts = queue_counts(Path(home_env))
            q = load_queue(Path(home_env))
            live_counts["workers_tracked"] = len(q.get("workers") or {})
            live_note = str(Path(home_env) / "queue.json")
        except Exception as e:
            live_note = f"live read err={type(e).__name__}"

    # Always simulate in a temp dir so dry-run never mutates live digest.
    with tempfile.TemporaryDirectory(prefix="jeeves-worker-state-") as td:
        demo = _simulate_ack_done(Path(td))

    return {
        "dry_run": True,
        "mutated": False,
        "ok": missing == [],
        "skill_complete": missing,
        "live_queue_path": live_note,
        "live_counts": live_counts,
        "after_ack": demo["after_ack"],
        "after_done": demo["after_done"],
        "rules": {
            "ACK": "accepted + busy + machines.<id>.working_on (TipForm)",
            "DONE": "done + idle + working_on cleared",
            "busy_signal": "webhook/TipForm from ACK/DONE — not TUI appearance (K12)",
            "seat_echo": "WORKING: FR owner/repo#n on ACK",
        },
        "commands": [
            "python -m jeeves.worker_state_tool --dry-run --json",
            "python -m jeeves worker-state --dry-run --json",
            "GET https://irc.ntsa.uk/bob/v1/report — machines.*.working_on / workers",
        ],
        "forbidden": [
            "infer busy from TUI appearance",
            "edit digest by hand for TipForm cache",
            "touch Ergo/BobIrcd",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Jeeves worker-state tool (FR #21): dry-run ACK/DONE busy path"
    )
    p.add_argument("--dry-run", action="store_true", help="Read-only skill + TipForm demo")
    p.add_argument("--json", action="store_true", help="JSON output (default with --dry-run)")
    p.add_argument("--digest-home", default="", help="Optional live digest for read-only counts")
    p.add_argument("--repo-root", default="", help="Repo root for skill path")
    args = p.parse_args(argv)

    if not args.dry_run:
        p.error("only --dry-run is supported in this seed")

    root = Path(args.repo_root) if args.repo_root else None
    home = Path(args.digest_home) if args.digest_home else None
    plan = run_dry_run(root, digest_home=home)
    print(json.dumps(plan, indent=2))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
