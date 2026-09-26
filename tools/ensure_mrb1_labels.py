#!/usr/bin/env python3
"""Ensure FR #151 MRB #1 labels exist on a GitHub repo (idempotent).

Intake appends ``needs-mrb1`` for kind issue/fr. Humans apply ``mrb1-pass`` or
``mrb1-reject``. Those labels must exist or GitHub issue create returns 422.

Example:
  python tools/ensure_mrb1_labels.py --repo SimonBarnett/gh-Jeeves
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

LABELS: list[tuple[str, str, str]] = [
    (
        "needs-mrb1",
        "FR #151: awaiting Simon vision/fit MRB #1",
        "FBCA04",
    ),
    (
        "mrb1-pass",
        "FR #151: MRB #1 fit approved — engineering allowed",
        "0E8A16",
    ),
    (
        "mrb1-reject",
        "FR #151: MRB #1 fit rejected — no engineering PR",
        "B60205",
    ),
]


def _gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def label_exists(repo: str, name: str) -> bool:
    proc = _gh(["api", f"repos/{repo}/labels/{name}"], check=False)
    return proc.returncode == 0


def ensure_label(repo: str, name: str, description: str, color: str) -> str:
    if label_exists(repo, name):
        return "exists"
    payload = json.dumps(
        {"name": name, "description": description, "color": color.lstrip("#")}
    )
    proc = subprocess.run(
        ["gh", "api", "--method", "POST", f"repos/{repo}/labels", "--input", "-"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        # race: created between check and create
        if label_exists(repo, name):
            return "exists"
        raise SystemExit(f"failed to create {name}: {proc.stderr or proc.stdout}")
    return "created"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ensure MRB #1 labels (FR #151)")
    p.add_argument("--repo", default="SimonBarnett/gh-Jeeves")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    for name, desc, color in LABELS:
        if args.dry_run:
            state = "exists" if label_exists(args.repo, name) else "missing"
            print(f"{name}: {state} (dry-run)")
            continue
        state = ensure_label(args.repo, name, desc, color)
        print(f"{name}: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
