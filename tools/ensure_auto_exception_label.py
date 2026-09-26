#!/usr/bin/env python3
"""Ensure FR #148 ``auto-exception`` label exists (idempotent).

``ExceptionReporter`` creates issues with ``labels=["auto-exception"]``.
GitHub returns HTTP 422 when the label is missing, so reports spool forever
as ``github_down`` until the label exists.

Example:
  python tools/ensure_auto_exception_label.py --repo SimonBarnett/gh-Jeeves
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

LABEL = "auto-exception"
DESCRIPTION = "FR #148: auto-filed deterministic exception report (no LLM)"
COLOR = "D93F0B"


def label_exists(repo: str, name: str) -> bool:
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/labels/{name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def ensure_label(repo: str) -> str:
    if label_exists(repo, LABEL):
        return "exists"
    payload = json.dumps(
        {"name": LABEL, "description": DESCRIPTION, "color": COLOR.lstrip("#")}
    )
    proc = subprocess.run(
        ["gh", "api", "--method", "POST", f"repos/{repo}/labels", "--input", "-"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        if label_exists(repo, LABEL):
            return "exists"
        raise SystemExit(f"failed to create {LABEL}: {proc.stderr or proc.stdout}")
    return "created"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ensure auto-exception label (FR #148)")
    p.add_argument("--repo", default="SimonBarnett/gh-Jeeves")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.dry_run:
        state = "exists" if label_exists(args.repo, LABEL) else "missing"
        print(f"{LABEL}: {state} (dry-run)")
        return 0
    print(f"{LABEL}: {ensure_label(args.repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
