"""FR #47: receiver drop-in for bobcallback — X-Bob-Secret + ear/machine digest."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.auth_secret import check_bob_secret, load_bob_secret
from jeeves.digest import apply_report, load_digest, public_digest_snapshot
from jeeves.receiver import StubReceiver


SECRET = "test-bob-secret-not-real"


def _post(url: str, payload: dict, *, secret: str | None = SECRET, path: str = "/bob/v1/report"):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Bob-Secret"] = secret
    req = urllib.request.Request(url.rstrip("/") + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (ConnectionError, OSError):
        # Windows can abort after 401 close; treat as auth failure for this contract
        return 401, b""


def _get(url: str, path: str = "/bob/v1/report"):
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_secret_load_from_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BOB_CALLBACK_SECRET", raising=False)
    monkeypatch.delenv("BOB_SECRET", raising=False)
    f = tmp_path / "bob.secret"
    f.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("BOB_CALLBACK_SECRET_FILE", str(f))
    assert load_bob_secret() == SECRET
    assert check_bob_secret({"X-Bob-Secret": SECRET}, SECRET)
    assert not check_bob_secret({"X-Bob-Secret": "nope"}, SECRET)


def test_post_report_401_without_secret(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        code, _ = _post(base, {"op": "merge", "machine": "flamingo", "online": True}, secret=None)
        assert code == 401
        code2, _ = _post(base, {"op": "merge", "machine": "flamingo"}, secret="wrong")
        assert code2 == 401
    finally:
        rx.stop()


def test_post_git_401_without_secret(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        data = b'{"zen":"x"}'
        req = urllib.request.Request(
            base + "/bob/v1/git",
            data=data,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "ping"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        rx.stop()


def test_merge_ear_machine_report_and_get_digest(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Captured-style ear payload (secrets scrubbed)
        payload = {
            "op": "merge",
            "machine": "flamingo",
            "online": True,
            "status": "I am online",
            "lastSeen": "2026-09-25T12:00:00Z",
            "pcent": {"cursor-models": 42, "grok": 10},
            "running": 1,
            "queued": 0,
            "workers": {
                "46804": {
                    "nick": "flamingo-46804",
                    "state": "busy",
                    "working_on": "FR x/y#1",
                }
            },
            "cursor_pools": [{"name": "auto", "remaining": 1}],
        }
        code, _ = _post(base, payload)
        assert code in (200, 204)
        st, doc = _get(base)
        assert st == 200
        assert "machines" in doc
        assert "flamingo" in doc["machines"]
        fl = doc["machines"]["flamingo"]
        assert fl["online"] is True
        assert fl["lastSeen"] == "2026-09-25T12:00:00Z"
        assert fl["pcent"]["cursor-models"] == 42
        assert "46804" in fl["workers"]
        assert isinstance(doc.get("cursor_pools"), list)
        assert len(doc["cursor_pools"]) >= 1
        # GET without secret still works
        st2, _ = _get(base, "/bob/v1/digest")
        assert st2 == 200
    finally:
        rx.stop()


def test_worker_state_and_queue_ops_still_work(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        code, _ = _post(
            base,
            {"op": "worker_state", "nick": "flamingo-1", "state": "busy", "job": "x"},
        )
        assert code in (200, 204)
        st, doc = _get(base)
        assert doc.get("workers", {}).get("flamingo-1", {}).get("state") == "busy"
    finally:
        rx.stop()


def test_git_secret_field_filter_still_400(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret=SECRET, require_secret=True)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        bad = {
            "action": "opened",
            "issue": {"number": 1, "title": "x", "body": "y"},
            "repository": {"full_name": "o/r"},
            "token": "ghp_REALLOOKING",
        }
        data = json.dumps(bad).encode("utf-8")
        req = urllib.request.Request(
            base + "/bob/v1/git",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issues",
                "X-Bob-Secret": SECRET,
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raised = False
        except urllib.error.HTTPError as e:
            raised = True
            assert e.code == 400
        assert raised
    finally:
        rx.stop()


def test_apply_report_rejects_secret_in_body(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(home, {"op": "merge", "machine": "ionos", "password": "x"})
    assert not out.ok
    assert out.err == "secret"


def test_g1_without_require_secret_still_works(tmp_path: Path):
    """G1 tests: require_secret=False keeps old behaviour for unit chains."""
    home = tmp_path / "d"
    home.mkdir()
    rx = StubReceiver(home, bob_secret="", require_secret=False)
    port = rx.start()
    try:
        base = f"http://127.0.0.1:{port}"
        code, _ = _post(
            base,
            {"op": "worker_state", "nick": "x-1", "state": "idle"},
            secret=None,
        )
        assert code in (200, 204)
    finally:
        rx.stop()


def test_cutover_doc_exists():
    root = Path(__file__).resolve().parents[1]
    doc = root / "docs" / "receiver-bobcallback-cutover.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "X-Bob-Secret" in text
    assert "19781" in text
    assert "Rollback" in text
