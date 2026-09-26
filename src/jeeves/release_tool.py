"""FR #22: jeeves-release skill helpers — dry-run tag/deploy plan.

Never Apply from FR workers. Never touch Ergo/BobIrcd.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

SKILL_MD = Path(__file__).resolve().parents[2] / "skills" / "jeeves-release" / "SKILL.md"
DEPLOY_PS1 = Path(__file__).resolve().parents[2] / "tools" / "Deploy-BobJeevesRelease.ps1"

REQUIRED_SKILL_HEADINGS = (
    "Commands",
    "Overlay",
)
REQUIRED_SKILL_MARKERS = (
    "name: jeeves-release",
    "Deploy-BobJeevesRelease.ps1",
    "python -m jeeves.release_tool",
    "JEEVES_RELEASE_TAG",
    "drift",
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
    if "DryRun" not in text and "--dry-run" not in text and "-DryRun" not in text:
        missing.append("missing:DryRun")
    if "G2" not in text and "g2" not in text.lower():
        missing.append("missing:G2")
    if "rollback" not in text.lower() and "Roll back" not in text:
        missing.append("missing:rollback")
    if "never" not in text.lower() and "Ergo" not in text and "BobIrcd" not in text:
        missing.append("missing:never-touch note")
    return missing


def _run_deploy_dry_run(repo_root: Path, timeout: float = 60.0) -> dict[str, Any]:
    script = repo_root / "tools" / "Deploy-BobJeevesRelease.ps1"
    if not script.is_file():
        return {"ok": False, "errors": ["Deploy-BobJeevesRelease.ps1 missing"], "dry_run": True}
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-DryRun",
            "-Json",
            "-RepoRoot",
            str(repo_root),
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    i = out.find("{")
    if i < 0:
        return {
            "ok": False,
            "dry_run": True,
            "errors": [f"non-json exit={proc.returncode} err={err[:200]!r} out={out[:300]!r}"],
        }
    plan = json.loads(out[i:])
    plan["_exit_code"] = proc.returncode
    return plan


def run_dry_run(repo_root: Path | None = None) -> dict[str, Any]:
    """Offline release plan: skill lint + Deploy -DryRun -Json + version/drift."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    missing = skill_complete(root / "skills" / "jeeves-release" / "SKILL.md")

    from .versioning import check_drift, running_version

    ver = running_version(root)
    drift = check_drift(root=root, allow_dirty=True)
    deploy = _run_deploy_dry_run(root)

    ok = missing == [] and bool(deploy.get("ok", True)) and bool(deploy.get("never_touch_ircd", True))
    return {
        "dry_run": True,
        "apply": False,
        "mutated": False,
        "ok": ok,
        "skill_complete": missing,
        "never_touch_ircd": True,
        "version": ver.report_string,
        "release_tag": ver.release_tag,
        "version_drift": drift.as_dict() if hasattr(drift, "as_dict") else {},
        "drift_ok": bool(getattr(drift, "ok", True)),
        "deploy_plan": {
            "tag": deploy.get("tag"),
            "steps": deploy.get("steps"),
            "forbidden": deploy.get("forbidden"),
            "never_touch_ircd": deploy.get("never_touch_ircd"),
            "ok": deploy.get("ok"),
            "errors": deploy.get("errors"),
        },
        "commands": [
            "python -m jeeves.release_tool --dry-run --json",
            "python -m jeeves release --dry-run --json",
            "powershell -File tools\\Deploy-BobJeevesRelease.ps1 -DryRun -Json",
            "# Apply (operators only): Deploy-BobJeevesRelease.ps1 -Apply -Tag vX.Y.Z",
        ],
        "forbidden": [
            "FR worker -Apply on production",
            "deploy from dirty hotpatch worktree",
            "touch Ergo/BobIrcd / ircd.yaml",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Jeeves release tool (FR #22): dry-run tag/deploy plan; never Apply here"
    )
    p.add_argument("--dry-run", action="store_true", help="Read-only skill + Deploy -DryRun")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--repo-root", default="", help="Repo root")
    args = p.parse_args(argv)

    if not args.dry_run:
        p.error("only --dry-run is supported in this seed (Apply stays Deploy-BobJeevesRelease.ps1)")

    root = Path(args.repo_root) if args.repo_root else None
    plan = run_dry_run(root)
    print(json.dumps(plan, indent=2))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
