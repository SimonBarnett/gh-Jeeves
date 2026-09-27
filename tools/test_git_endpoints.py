#!/usr/bin/env python3
"""Smoke: Git webhook group (POST /bob/v1/git). Default base https://bob.ntsa.uk (FR #204).

Safe writes: synthetic ping/issue payloads with unique markers. Does not claim
queue rows as a worker. Idempotent: ping + throwaway issue events only.

Cleanup: none required (receiver stores announce/queue from synthetic repos that
should be ignored or non-fleet; prefer --base-url local StubReceiver in CI).
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_common import (  # noqa: E402
    DEFAULT_BASE_URL,
    CheckResult,
    add_base_args,
    base_url_from_env,
    finish,
    json_body,
    parse_json,
    request,
)


def run(base: str, *, timeout: float = 15.0) -> CheckResult:
    r = CheckResult("git")
    url = f"{base.rstrip('/')}/bob/v1/git"
    marker = f"fr204-smoke-{uuid.uuid4().hex[:10]}"

    # Missing event header → 400
    try:
        code, _, body = request(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            body=json_body({"zen": "x", "hook_id": 1}),
            timeout=timeout,
        )
        r.add("missing X-GitHub-Event → 400", code == 400, f"status={code} body={body[:80]!r}")
    except ConnectionError as e:
        r.add("missing X-GitHub-Event → 400", False, str(e))

    # Malformed JSON → 400
    try:
        code, _, body = request(
            "POST",
            url,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
            },
            body=b"{not-json",
            timeout=timeout,
        )
        r.add("malformed JSON → 400", code == 400, f"status={code}")
    except ConnectionError as e:
        r.add("malformed JSON → 400", False, str(e))

    # Valid ping → 204
    try:
        code, _, _ = request(
            "POST",
            url,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
            },
            body=json_body(
                {
                    "zen": marker,
                    "hook_id": int(time.time()) % 1_000_000,
                    "repository": {"full_name": "SimonBarnett/gh-Jeeves-smoke-ignore"},
                }
            ),
            timeout=timeout,
        )
        r.add("ping → 204", code == 204, f"status={code}")
    except ConnectionError as e:
        r.add("ping → 204", False, str(e))

    # Read-side: public digest still reachable (no secret)
    try:
        code, _, data = request("GET", f"{base.rstrip('/')}/bob/v1/report", timeout=timeout)
        obj = parse_json(data)
        r.add(
            "GET /bob/v1/report after git",
            code == 200 and isinstance(obj, dict),
            f"status={code} keys={list(obj)[:8] if isinstance(obj, dict) else type(obj)}",
        )
    except ConnectionError as e:
        r.add("GET /bob/v1/report after git", False, str(e))

    return r


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    add_base_args(p)
    args = p.parse_args(argv)
    base = base_url_from_env(args.base_url)
    assert DEFAULT_BASE_URL == "https://bob.ntsa.uk"
    try:
        result = run(base, timeout=args.timeout)
    except Exception as e:  # noqa: BLE001 — CLI must not traceback
        result = CheckResult("git")
        result.add("fatal", False, str(e))
    return finish(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
