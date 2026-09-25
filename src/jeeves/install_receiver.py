"""FR #9 / K8: BobReport receiver installer helpers (no live SCM from tests)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "tools" / "Install-BobReport.ps1"
START_PS1 = REPO_ROOT / "tools" / "Start-BobReport.ps1"


def installer_paths_ok(root: Path | None = None) -> list[str]:
    root = Path(root or REPO_ROOT)
    missing: list[str] = []
    for rel in (
        "tools/Install-BobReport.ps1",
        "tools/Start-BobReport.ps1",
        "src/jeeves/prod_receiver.py",
        "src/jeeves/__main__.py",
    ):
        if not (root / rel).is_file():
            missing.append(rel)
    return missing


def installer_never_touches_ircd(path: Path | None = None) -> list[str]:
    path = path or INSTALL_PS1
    if not path.is_file():
        return ["installer missing"]
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    if re.search(r"(?i)&\s*sc\.exe\s+(create|config|start|stop|delete)\s+BobIrcd", text):
        hits.append("sc_BobIrcd")
    if re.search(r"(?i)Install-BobIrcd\.ps1", text) and "forbidden" not in text.lower():
        hits.append("Install-BobIrcd")
    return hits


def run_install_dry_run(repo_root: Path | None = None, timeout: float = 60.0) -> dict[str, Any]:
    root = Path(repo_root or REPO_ROOT)
    script = root / "tools" / "Install-BobReport.ps1"
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
    proc = subprocess.run(ps, capture_output=True, timeout=timeout, check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    i = out.find("{")
    if i < 0:
        raise RuntimeError(f"dry-run non-json exit={proc.returncode} err={err!r} out={out[:400]!r}")
    data = json.loads(out[i:])
    data["_exit_code"] = proc.returncode
    data["_stderr"] = err
    return data


def run_start_dry_run(repo_root: Path | None = None, timeout: float = 30.0) -> dict[str, Any]:
    root = Path(repo_root or REPO_ROOT)
    script = root / "tools" / "Start-BobReport.ps1"
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
