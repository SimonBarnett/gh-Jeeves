#!/usr/bin/env python3
"""Post FR #92 mrb/verdict check run for a PR (reviewing seat only).

Example:
  python tools/post_mrb_verdict.py --repo SimonBarnett/gh-Jeeves --pr 90 \\
    --reviewer-seat flamingo-43052 \\
    --pytest-cmd "python -m pytest tests/ -q" --pytest-exit 0 \\
    --duration-s 720 --verdict PASS

Requires gh auth with repo scope. Branch protection must require check
``mrb/verdict`` (see docs/mrb-enforcement.md).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# allow running from repo root without install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jeeves.mrb_gate import (  # noqa: E402
    CHECK_NAME,
    DEFAULT_MIN_DURATION_S,
    MrbVerdict,
    finalize_check_conclusion,
    parse_seat_trailer,
    verdict_to_check_output,
)


def _gh_json(args: list[str]) -> dict:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh failed: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout or "{}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Post mrb/verdict check (FR #92)")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--reviewer-seat", required=True, help="this seat nick")
    p.add_argument("--author-seat", default="", help="override Seat: from PR body")
    p.add_argument("--verdict", required=True, choices=["PASS", "FAIL", "pass", "fail"])
    p.add_argument("--pytest-cmd", required=True)
    p.add_argument("--pytest-exit", type=int, required=True)
    p.add_argument("--duration-s", type=float, required=True)
    p.add_argument(
        "--min-duration-s",
        type=int,
        default=DEFAULT_MIN_DURATION_S,
        help=f"default {DEFAULT_MIN_DURATION_S} (10 min)",
    )
    p.add_argument("--notes", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    pr = _gh_json(
        [
            "pr",
            "view",
            str(args.pr),
            "--repo",
            args.repo,
            "--json",
            "number,body,headRefOid,url,title",
        ]
    )
    head = str(pr.get("headRefOid") or "")
    author = (args.author_seat or "").strip() or (parse_seat_trailer(pr.get("body") or "") or "")
    if not author:
        raise SystemExit(
            "author seat unknown: put 'Seat: {nick}' in the PR body or pass --author-seat"
        )

    v = MrbVerdict(
        pr=int(args.pr),
        head_sha=head,
        author_seat=author,
        reviewer_seat=args.reviewer_seat,
        verdict=args.verdict.upper(),
        pytest_cmd=args.pytest_cmd,
        pytest_exit=int(args.pytest_exit),
        duration_s=float(args.duration_s),
        notes=args.notes,
        min_duration_s=int(args.min_duration_s),
    )
    conclusion, errors = finalize_check_conclusion(v)
    output = verdict_to_check_output(v, errors)

    payload = {
        "name": CHECK_NAME,
        "head_sha": v.head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": output,
    }
    print(json.dumps({"conclusion": conclusion, "errors": errors, "payload": payload}, indent=2))
    if errors and conclusion == "failure":
        print("REFUSING to post success; posting failure check", file=sys.stderr)
    if args.dry_run:
        return 0 if not errors else 2

    owner, name = args.repo.split("/", 1)
    api = [
        "api",
        "--method",
        "POST",
        f"repos/{owner}/{name}/check-runs",
        "--input",
        "-",
    ]
    proc = subprocess.run(
        ["gh", *api],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode
    print(proc.stdout)
    return 0 if conclusion in ("success", "neutral") and not (
        conclusion == "failure"
    ) else (0 if conclusion != "failure" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
