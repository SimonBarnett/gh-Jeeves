#!/usr/bin/env python3
"""Smoke: Jira webhook group (POST/GET /bob/v1/jira). Default https://bob.ntsa.uk (FR #204).

Uses reserved synthetic key FR204-SMOKE-* . Requires BOB_SECRET when auth enabled.
Live: skip writes clearly if secret absent (exit 0 with skip in JSON only when
--allow-skip; default fail soft as check ok=false labeled skip).

Cleanup: synthetic tickets remain under jira/tickets.json; key namespace is reserved.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_common import (  # noqa: E402
    DEFAULT_BASE_URL,
    CheckResult,
    add_base_args,
    base_url_from_env,
    finish,
    get_secret,
    json_body,
    parse_json,
    request,
)


def _jira_issue_payload(key: str) -> dict:
    return {
        "issue": {
            "key": key,
            "fields": {
                "summary": f"[FR204-SMOKE] synthetic {key}",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Task"},
                "project": {"key": "FR204"},
            },
        },
        "webhookEvent": "jira:issue_created",
    }


def run(base: str, *, timeout: float = 15.0) -> CheckResult:
    r = CheckResult("jira")
    root = base.rstrip("/")
    post_url = f"{root}/bob/v1/jira"
    get_url = f"{root}/bob/v1/jira"
    secret = get_secret()
    key = f"FR204-SMOKE-{uuid.uuid4().hex[:8].upper()}"

    # Missing secret → 401 when server requires it (StubReceiver with secret)
    try:
        code, _, _ = request(
            "GET",
            get_url,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        # Without secret: 401 if required, or 200 empty if open (local misconfig)
        r.add(
            "GET without secret",
            code in (401, 200),
            f"status={code}",
        )
    except ConnectionError as e:
        r.add("GET without secret", False, str(e))

    if not secret:
        r.add(
            "secret present for write/read",
            False,
            "set BOB_SECRET (skip live writes — credential absent)",
        )
        return r

    hdr = {
        "Content-Type": "application/json",
        "X-Bob-Secret": secret,
    }
    bad = {
        "Content-Type": "application/json",
        "X-Bob-Secret": "definitely-wrong-secret-fr204",
    }

    try:
        code, _, _ = request("GET", get_url, headers=bad, timeout=timeout)
        r.add("GET invalid secret → 401", code == 401, f"status={code}")
    except ConnectionError as e:
        r.add(
            "GET invalid secret → 401",
            "10053" in str(e) or "10054" in str(e) or "forcibly" in str(e).lower(),
            str(e),
        )

    try:
        code, _, _ = request(
            "POST",
            post_url,
            headers=hdr,
            body=json_body(_jira_issue_payload(key)),
            timeout=timeout,
        )
        r.add("POST synthetic issue → 204", code == 204, f"status={code} key={key}")
    except ConnectionError as e:
        r.add("POST synthetic issue → 204", False, str(e))

    try:
        code, _, data = request("GET", get_url, headers=hdr, timeout=timeout)
        obj = parse_json(data) or {}
        tickets = obj.get("tickets") if isinstance(obj, dict) else None
        found = isinstance(tickets, dict) and (
            key in tickets
            or any(key in str(k) or key in str(v) for k, v in tickets.items())
        )
        r.add(
            "GET round-trip contains synthetic key",
            code == 200 and found,
            f"status={code} found={found}",
        )
        if isinstance(obj, dict):
            blob = json_body(obj).decode("utf-8", errors="replace")
            r.add("GET body has no secret echo", secret not in blob, "")
    except ConnectionError as e:
        r.add("GET round-trip contains synthetic key", False, str(e))

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
    except Exception as e:  # noqa: BLE001
        result = CheckResult("jira")
        result.add("fatal", False, str(e))
    return finish(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
