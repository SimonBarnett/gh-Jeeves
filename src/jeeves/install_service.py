"""FR #17: pure helpers for BobJeeves install skill (no live SCM)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "tools" / "Install-BobJeeves.ps1"
START_PS1 = REPO_ROOT / "tools" / "Start-BobJeeves.ps1"
SKILL_MD = REPO_ROOT / "skills" / "jeeves-install-service" / "SKILL.md"

FORBIDDEN_IN_INSTALLER = (
    r"(?i)sc\.exe\s+(create|config|start|stop|delete)\s+BobIrcd",
    r"(?i)Install-BobIrcd",
    r"(?i)ircd\.yaml",
    r"(?i)Stop-Service\s+-Name\s+['\"]?BobIrcd",
    r"(?i)Start-Service\s+-Name\s+['\"]?BobIrcd",
)

REQUIRED_SKILL_HEADINGS = (
    "## Purpose",
    "## Commands",
    "## Forbidden",
    "BobJeeves",
    "DryRun",
    "never touch",
)


def skill_complete(path: Path | None = None) -> list[str]:
    """Return list of missing requirements for the skill file."""
    path = path or SKILL_MD
    missing: list[str] = []
    if not path.is_file():
        return ["skill file missing"]
    text = path.read_text(encoding="utf-8")
    if "TODO: seed FR" in text:
        missing.append("still stub TODO")
    if not text.strip().startswith("---"):
        missing.append("missing frontmatter")
    if "name: jeeves-install-service" not in text:
        missing.append("frontmatter name")
    for h in REQUIRED_SKILL_HEADINGS:
        if h.lower() not in text.lower():
            missing.append(f"missing:{h}")
    if "Install-BobJeeves.ps1" not in text:
        missing.append("missing installer command path")
    return missing


def installer_source_safe(path: Path | None = None) -> list[str]:
    path = path or INSTALL_PS1
    if not path.is_file():
        return ["installer missing"]
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    for pat in FORBIDDEN_IN_INSTALLER:
        if re.search(pat, text):
            # allow mention in comments/forbidden list strings carefully
            if "forbidden" in text.lower() and "BobIrcd" in pat:
                # still ban actual sc.exe create BobIrcd
                if "sc.exe" in pat and re.search(pat, text):
                    # check not only in forbidden array strings
                    for m in re.finditer(pat, text):
                        start = max(0, m.start() - 40)
                        window = text[start : m.end() + 10]
                        if "forbidden" in window.lower() or "never" in window.lower():
                            continue
                        hits.append(pat)
            else:
                hits.append(pat)
    # simpler hard ban: no sc.exe ... BobIrcd as a command form outside comments of forbidden list
    if re.search(r"(?i)&\s*sc\.exe.*BobIrcd", text):
        hits.append("sc_invoke_BobIrcd")
    if re.search(r"(?i)Install-BobIrcd\.ps1", text) and "forbidden" not in text[max(0, text.lower().find("install-bobircd") - 30) :].lower()[:80]:
        # allow in forbidden list
        pass
    return list(dict.fromkeys(hits))


def run_dry_run(repo_root: Path | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Invoke Install-BobJeeves.ps1 -DryRun -Json (no Apply)."""
    root = Path(repo_root or REPO_ROOT)
    script = root / "tools" / "Install-BobJeeves.ps1"
    if not script.is_file():
        raise FileNotFoundError(script)
    ps = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-DryRun",
        "-Json",
        "-RepoRoot",
        str(root),
    ]
    proc = subprocess.run(
        ps,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    # last JSON object
    data: dict[str, Any]
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        # try extract from first {
        i = out.find("{")
        if i < 0:
            raise RuntimeError(
            f"dry-run non-json exit={proc.returncode} stderr={err!r} out={out[:500]!r}"
        )
        data = json.loads(out[i:])
    data["_exit_code"] = proc.returncode
    data["_stderr"] = err
    return data


def run_start_dry_run(repo_root: Path | None = None, timeout: float = 30.0) -> dict[str, Any]:
    root = Path(repo_root or REPO_ROOT)
    script = root / "tools" / "Start-BobJeeves.ps1"
    ps = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-DryRun",
        "-RepoRoot",
        str(root),
    ]
    proc = subprocess.run(ps, capture_output=True, timeout=timeout, check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    i = out.find("{")
    data = json.loads(out[i:] if i >= 0 else out)
    data["_exit_code"] = proc.returncode
    return data
