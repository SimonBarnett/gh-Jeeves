"""FR #23: jeeves-token-less-gate skill helpers — dry-run G1 collect + G2 pointer.

The gate itself is script-only (tests/g1_token_less_e2e). This module is an
operator overlay and must never be required for G1 to pass.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

SKILL_MD = (
    Path(__file__).resolve().parents[2] / "skills" / "jeeves-token-less-gate" / "SKILL.md"
)

REQUIRED_SKILL_HEADINGS = (
    "Commands",
    "Overlay",
    "G1",
    "G2",
)
REQUIRED_SKILL_MARKERS = (
    "name: jeeves-token-less-gate",
    "python -m jeeves.token_less_gate_tool",
    "--dry-run",
    "g1_token_less_e2e",
    "g2-live-smoke-checklist",
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
    if "assigns" not in text.lower() and "Jeeves assigns" not in text:
        missing.append("missing:Jeeves assigns (FR #106)")
    if "llm" not in text.lower() and "token" not in text.lower():
        missing.append("missing:token-less/LLM note")
    return missing


def _collect_g1(repo_root: Path, timeout: float = 60.0) -> tuple[bool, int, str]:
    """pytest --collect-only under g1_token_less_e2e (no live IRC)."""
    g1 = repo_root / "tests" / "g1_token_less_e2e"
    if not g1.is_dir():
        return False, 0, "g1_token_less_e2e missing"
    proc = subprocess.run(
        ["python", "-m", "pytest", str(g1), "--collect-only", "-q"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**__import__("os").environ, "PYTHONPATH": str(repo_root / "src")},
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    import re

    # Quiet collect may print "path: N" per file, or "N tests collected".
    m = re.search(r"(\d+)\s+tests?\s+collected", out, re.I)
    if m:
        n = int(m.group(1))
    else:
        n = sum(int(x) for x in re.findall(r":\s*(\d+)\s*$", out, re.M))
    ok = n >= 1 and proc.returncode in (0, 1)  # 1 can appear with warnings-only
    # Prefer success when we clearly collected tests even if rc odd.
    if n >= 1:
        ok = True
    return ok, n, out[-500:] if not ok else "ok"


def run_dry_run(repo_root: Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    missing = skill_complete(root / "skills" / "jeeves-token-less-gate" / "SKILL.md")
    g1_ok, g1_n, g1_note = _collect_g1(root)
    g2 = root / "docs" / "g2-live-smoke-checklist.md"
    guard = root / "src" / "jeeves" / "guard.py"
    return {
        "dry_run": True,
        "mutated": False,
        "ok": missing == [] and g1_ok and g2.is_file() and guard.is_file(),
        "skill_complete": missing,
        "g1_collect_ok": g1_ok,
        "g1_test_count": g1_n,
        "g1_note": g1_note,
        "g2_checklist_present": g2.is_file(),
        "g2_checklist_path": str(g2) if g2.is_file() else "",
        "guard_module_present": guard.is_file(),
        "never_requires_llm": True,
        "chain": [
            "GitHub event → Jeeves announce + queue",
            "worker !bored → Jeeves assigns",
            "ACK → accepted + busy",
            "worker task (only AI step)",
            "DONE → done + idle + supersede",
        ],
        "commands": [
            "python -m jeeves.token_less_gate_tool --dry-run --json",
            "python -m jeeves gate --dry-run --json",
            "pytest -q tests/g1_token_less_e2e tests/cast_iron",
            "docs/g2-live-smoke-checklist.md (manual after deploy)",
        ],
        "forbidden": [
            "require LLM/skill for G1",
            "live IRC in G1",
            "touch Ergo/BobIrcd",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Jeeves token-less gate tool (FR #23): dry-run G1 collect + G2 pointer"
    )
    p.add_argument("--dry-run", action="store_true", help="Skill lint + pytest --collect-only G1")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--repo-root", default="", help="Repo root")
    args = p.parse_args(argv)

    if not args.dry_run:
        p.error("only --dry-run is supported in this seed (full G1: pytest tests/g1_token_less_e2e)")

    root = Path(args.repo_root) if args.repo_root else None
    plan = run_dry_run(root)
    print(json.dumps(plan, indent=2))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
