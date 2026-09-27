"""FR #191: external reporting mode on POST /bob/v1/report."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from jeeves.digest import (
    apply_report,
    is_external_report,
    load_digest,
    public_digest_snapshot,
)
from jeeves.receiver import EXTERNAL_REPORT_RATE_PER_MIN, StubReceiver


def _post(url: str, payload: dict, *, headers: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_is_external_flags():
    assert is_external_report({"external": True, "id": "x"})
    assert is_external_report({"external": "true", "id": "x"})
    assert is_external_report({"source": "external", "id": "x"})
    assert is_external_report({"source": {"kind": "external"}, "id": "x"})
    assert not is_external_report({"op": "merge", "machine": "ionos"})
    assert not is_external_report({"machine": "ionos", "online": True})


def test_external_stored_separately_from_fleet_machines(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    # Seed a fleet machine value that must not be overwritten.
    apply_report(home, {"op": "merge", "machine": "ionos", "working_on": "fleet-job", "online": True})
    before = load_digest(home)["machines"]["ionos"]["working_on"]
    assert before == "fleet-job"

    out = apply_report(
        home,
        {
            "external": True,
            "id": "ionos",  # same name as fleet seat — must not collide
            "working_on": "simon-tooling",
            "status": "ok",
            "pcent": {"auto": 42},
        },
    )
    assert out.ok and out.changed
    assert out.announce.startswith("EXT-REPORT ionos")
    doc = load_digest(home)
    assert doc["machines"]["ionos"]["working_on"] == "fleet-job"
    assert "ionos" in doc["external_reports"]
    assert doc["external_reports"]["ionos"]["working_on"] == "simon-tooling"
    assert doc["external_reports"]["ionos"]["source"] == "external"
    assert doc["external_reports"]["ionos"]["pcent"]["auto"] == 42
    assert (home / "external" / "ionos.json").is_file()

    snap = public_digest_snapshot(home)
    assert "external_reports" in snap
    assert snap["external_reports"]["ionos"]["working_on"] == "simon-tooling"
    # fleet path unchanged shape
    assert "ionos" in snap["machines"]


def test_fleet_report_path_unchanged(tmp_path: Path):
    home = tmp_path / "f"
    home.mkdir()
    out = apply_report(
        home, {"op": "merge", "machine": "flamingo", "working_on": "FR x", "online": True}
    )
    assert out.ok and out.changed
    assert not out.announce
    doc = load_digest(home)
    assert doc["machines"]["flamingo"]["working_on"] == "FR x"
    assert doc.get("external_reports") in ({}, None) or "flamingo" not in (
        doc.get("external_reports") or {}
    )


def test_http_external_secret_and_outbox(tmp_path: Path):
    home = tmp_path / "rx"
    home.mkdir()
    rx = StubReceiver(home, bob_secret="fleet-secret", require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        code_bad, _ = _post(
            f"{base}/bob/v1/report",
            {"external": True, "id": "tool-a", "status": "hi"},
        )
        assert code_bad == 401

        code, _ = _post(
            f"{base}/bob/v1/report",
            {"external": True, "id": "tool-a", "status": "progress", "working_on": "batch"},
            headers={"X-Bob-Secret": "fleet-secret"},
        )
        assert code in (200, 204)
        outbox = (home / "chair-outbox.txt").read_text(encoding="utf-8")
        assert "EXT-REPORT tool-a" in outbox
        assert "PRIVMSG #bobiverse :" in outbox
        snap = rx.state.snapshot()
        assert snap["external_reports"]["tool-a"]["status"] == "progress"
    finally:
        rx.stop()


def test_external_rate_limit_429(tmp_path: Path):
    home = tmp_path / "rl"
    home.mkdir()
    rx = StubReceiver(
        home,
        bob_secret="",
        require_secret=False,
        external_report_rate_per_min=3,
    )
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        codes = []
        for i in range(5):
            code, body = _post(
                f"{base}/bob/v1/report",
                {"external": True, "id": f"r{i}", "status": "x"},
            )
            codes.append(code)
            if code == 429:
                obj = json.loads(body.decode("utf-8") or "{}")
                assert obj.get("error") == "rate_limited"
        assert 429 in codes
        assert codes.count(429) >= 1
        assert sum(1 for c in codes if c in (200, 204)) <= 3
    finally:
        rx.stop()


def test_external_rate_constant():
    assert EXTERNAL_REPORT_RATE_PER_MIN == 10
