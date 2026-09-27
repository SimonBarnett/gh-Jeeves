#!/usr/bin/env python3
"""Smoke: intake group (POST /bob/v1/intake, GET /bob/v1/intake/{id}). Default bob.ntsa.uk.

Uses synthetic idempotency keys fr204-*. Requires BOB_SECRET (and optional
BOB_INTAKE_KEY when fleet key configured). Safe: allow-listed repo + FakeGitHubFiler
on StubReceiver; live host may queue/file — use unique keys only.
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
    get_intake_key,
    get_secret,
    json_body,
    parse_json,
    request,
)


def run(base: str, *, timeout: float = 15.0) -> CheckResult:
    r = CheckResult("intake")
    root = base.rstrip("/")
    secret = get_secret()
    if not secret:
        r.add("BOB_SECRET", False, "credential absent — cannot exercise intake writes")
        return r

    idem = f"fr204-{uuid.uuid4().hex}"
    payload = {
        "kind": "issue",
        "repo": "SimonBarnett/gh-Jeeves",
        "title": "harvest: FR204 smoke (safe delete)",
        "body": "## What\n\nSynthetic FR204 smoke — ignore/close.\n",
        "source": {
            "machine": "smoke",
            "agent": "fr204",
            "skill_book": "gh-Jeeves",
            "version": "",
        },
        "idempotency_key": idem,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Bob-Secret": secret,
    }
    ik = get_intake_key()
    if ik:
        headers["X-Bob-Intake-Key"] = ik

    # Auth failure
    try:
        code, _, _ = request(
            "POST",
            f"{root}/bob/v1/intake",
            headers={
                "Content-Type": "application/json",
                "X-Bob-Secret": "wrong",
            },
            body=json_body(payload),
            timeout=timeout,
        )
        r.add("POST wrong secret → 401", code == 401, f"status={code}")
    except ConnectionError as e:
        r.add(
            "POST wrong secret → 401",
            "10053" in str(e) or "10054" in str(e) or "forcibly" in str(e).lower(),
            str(e),
        )

    # Rejected: bad kind / empty
    try:
        code, _, data = request(
            "POST",
            f"{root}/bob/v1/intake/",
            headers=headers,
            body=json_body({"kind": "nope", "repo": "x/y", "title": "t", "body": "b"}),
            timeout=timeout,
        )
        r.add("POST rejected kind", code >= 400, f"status={code}")
    except ConnectionError as e:
        r.add("POST rejected kind", False, str(e))

    # Accepted (or queued)
    intake_id = ""
    try:
        code, _, data = request(
            "POST",
            f"{root}/bob/v1/intake",
            headers=headers,
            body=json_body(payload),
            timeout=timeout,
        )
        obj = parse_json(data) or {}
        intake_id = str(obj.get("intake_id") or "") if isinstance(obj, dict) else ""
        r.add(
            "POST accepted/queued",
            code in (200, 202) and bool(intake_id),
            f"status={code} id={intake_id[:16]}",
        )
    except ConnectionError as e:
        r.add("POST accepted/queued", False, str(e))

    if intake_id:
        try:
            code, _, data = request(
                "GET",
                f"{root}/bob/v1/intake/{intake_id}",
                timeout=timeout,
            )
            obj = parse_json(data) or {}
            r.add(
                "GET intake status",
                code == 200 and isinstance(obj, dict),
                f"status={code}",
            )
        except ConnectionError as e:
            r.add("GET intake status", False, str(e))

        # Idempotent retry
        try:
            code2, _, data2 = request(
                "POST",
                f"{root}/bob/v1/intake",
                headers=headers,
                body=json_body(payload),
                timeout=timeout,
            )
            obj2 = parse_json(data2) or {}
            same = isinstance(obj2, dict) and str(obj2.get("intake_id") or "") == intake_id
            r.add("idempotent retry same id", code2 in (200, 202) and same, f"status={code2}")
        except ConnectionError as e:
            r.add("idempotent retry same id", False, str(e))

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
        result = CheckResult("intake")
        result.add("fatal", False, str(e))
    return finish(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
