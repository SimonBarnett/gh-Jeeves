"""FR #73: rotating service log + structured event= INFO lines (no secrets)."""

from __future__ import annotations

import logging
from pathlib import Path

import jeeves.service_log as slog
from jeeves.channel_join import AutoJoinController
from jeeves.roles import _log_announce_line
from jeeves.service_log import configure_service_logging, event, service_log_path


class _FakeClient:
    def __init__(self) -> None:
        self.raw: list[str] = []
        self.joined: list[str] = []

    def send_raw(self, line: str) -> None:
        self.raw.append(line)

    def join(self, *chans: str) -> None:
        self.joined.extend(chans)


def test_configure_writes_rotating_log(tmp_path: Path, monkeypatch):
    # reset module flag so configure runs again
    monkeypatch.setattr(slog, "_configured", False)
    path = configure_service_logging(tmp_path, max_bytes=50_000, backup_count=2)
    assert path == tmp_path / "bobjeeves-service.log"
    assert path.is_file() or path.parent.is_dir()
    logging.getLogger("jeeves.test").info("event=probe ok=1")
    # force handlers flush
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "event=service_log" in text or "event=probe" in text
    assert path.name == "bobjeeves-service.log"


def test_event_redacts_secret_markers(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setattr(slog, "_configured", False)
    configure_service_logging(tmp_path)
    log = logging.getLogger("jeeves.redact")
    with caplog.at_level(logging.INFO, logger="jeeves.redact"):
        event(log, "auth", token="ghp_NOTAREALTOKEN1234567890", result="sent")
    joined = " ".join(r.message for r in caplog.records)
    assert "event=auth" in joined
    assert "ghp_" not in joined
    assert "redacted" in joined


def test_announce_log_line_extracts_repo_mode(caplog):
    with caplog.at_level(logging.INFO, logger="jeeves.chair"):
        _log_announce_line("GIT opened SimonBarnett/gh-Jeeves#71 FR: outbox pos")
    msg = " ".join(r.message for r in caplog.records)
    assert "event=announce" in msg
    assert "SimonBarnett/gh-Jeeves#71" in msg or (
        "repo=SimonBarnett/gh-Jeeves#71" in msg
    )
    assert "mode=FR" in msg or "mode=GIT" in msg


def test_list_finish_logs_channel_count(caplog):
    client = _FakeClient()
    ctrl = AutoJoinController(client, nick="Jeeves", seed=["#bobiverse"], list_interval_s=9999)
    ctrl._list_buf = {"#bobiverse", "#flamingo", "#ionos"}
    with caplog.at_level(logging.INFO, logger="jeeves.autojoin"):
        ctrl._finish_list()
    msg = " ".join(r.message for r in caplog.records)
    assert "event=list" in msg
    assert "channels=3" in msg


def test_service_log_path_default_name():
    p = service_log_path(Path("/tmp/jeeves-home-x"))
    assert p.name == "bobjeeves-service.log"
