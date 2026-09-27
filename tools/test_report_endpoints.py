#!/usr/bin/env python3
"""Smoke: report/digest group. Default https://bob.ntsa.uk (FR #204).

GET aliases are public. POST uses external:true + unique id so fleet machines.*
are never overwritten (FR #191). Requires BOB_SECRET when auth enabled.
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


def run(base: str, *, timeout: float = 15.0) -> CheckResult:
    r = CheckResult("report")
    root = base.rstrip("/")
    for path in ("/bob/v1/report", "/bob/v1/digest", "/digest"):
        try:
            code, hdrs, data = request("GET", f"{root}{path}", timeout=timeout)
            obj = parse_json(data)
            ct_ok = "application/json" in (hdrs.get("content-type") or "")
            r.add(
                f"GET {path}",
                code == 200 and isinstance(obj, dict) and ct_ok,
                f"status={code}",
            )
            if isinstance(obj, dict):
                r.add(
                    f"GET {path} has machines",
                    isinstance(obj.get("machines"), dict),
                    "",
                )
        except ConnectionError as e:
            r.add(f"GET {path}", False, str(e))

    secret = get_secret()
    if not secret:
        r.add("POST /bob/v1/report", False, "BOB_SECRET absent — cannot auth write")
        return r

    eid = f"fr204-{uuid.uuid4().hex[:10]}"
    payload = {
        "external": True,
        "id": eid,
        "status": "smoke-ok",
        "working_on": "FR204 endpoint smoke",
        "online": True,
    }
    try:
        code, _, _ = request(
            "POST",
            f"{root}/bob/v1/report",
            headers={
                "Content-Type": "application/json",
                "X-Bob-Secret": "wrong-secret-fr204",
            },
            body=json_body(payload),
            timeout=timeout,
        )
        r.add("POST wrong secret → 401", code == 401, f"status={code}")
    except ConnectionError as e:
        # Windows StubReceiver sometimes aborts the socket after 401.
        r.add(
            "POST wrong secret → 401",
            "10053" in str(e) or "10054" in str(e) or "forcibly" in str(e).lower(),
            str(e),
        )

    try:
        code, _, _ = request(
            "POST",
            f"{root}/bob/v1/report",
            headers={
                "Content-Type": "application/json",
                "X-Bob-Secret": secret,
            },
            body=json_body(payload),
            timeout=timeout,
        )
        r.add("POST external report", code in (200, 204), f"status={code}")
    except ConnectionError as e:
        r.add("POST external report", False, str(e))

    try:
        code, _, data = request("GET", f"{root}/bob/v1/report", timeout=timeout)
        obj = parse_json(data) or {}
        ext = obj.get("external_reports") if isinstance(obj, dict) else None
        found = isinstance(ext, dict) and eid in ext
        r.add(
            "GET shows external_reports id",
            code == 200 and found,
            f"found={found}",
        )
        # Must not have stomped a fleet seat named like smoke id
        machines = obj.get("machines") if isinstance(obj, dict) else {}
        if isinstance(machines, dict) and eid in machines:
            r.add("did not write fleet machines", False, f"machines has {eid}")
        else:
            r.add("did not write fleet machines", True, "")
    except ConnectionError as e:
        r.add("GET shows external_reports id", False, str(e))

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
        result = CheckResult("report")
        result.add("fatal", False, str(e))
    return finish(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
