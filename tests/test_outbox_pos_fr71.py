"""FR #71: migrate chair-outbox.txt.pos → chair-outbox.pos; never replay on cutover."""

from __future__ import annotations

from pathlib import Path

from jeeves.outbox_pos import (
    LEGACY_POS_NAME,
    POS_NAME,
    resolve_outbox_start,
    write_pos,
)
from jeeves.roles import JeevesChair


class _FakeClient:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []
        self.joined: list[str] = []

    def join(self, *chans: str) -> None:
        self.joined.extend(chans)

    def privmsg(self, target: str, text: str) -> None:
        self.msgs.append((target, text))

    def send_raw(self, line: str) -> None:  # pragma: no cover
        pass


def test_migrate_legacy_pos_file(tmp_path: Path):
    home = tmp_path
    out = home / "chair-outbox.txt"
    body = b"PRIVMSG #bobiverse :old1\nPRIVMSG #bobiverse :old2\n"
    out.write_bytes(body)
    # legacy agentic_irc already drained through first line boundary-ish
    mid = body.find(b"\n") + 1
    (home / LEGACY_POS_NAME).write_text(str(mid), encoding="utf-8")

    start = resolve_outbox_start(home, outbox_size=len(body), replay=False)
    assert start == mid
    assert (home / POS_NAME).is_file()
    assert (home / POS_NAME).read_text(encoding="utf-8").strip() == str(mid)


def test_neither_pos_nonempty_starts_at_eof(tmp_path: Path):
    home = tmp_path
    body = b"PRIVMSG #bobiverse :historic-flood-risk\n" * 50
    (home / "chair-outbox.txt").write_bytes(body)
    assert not (home / POS_NAME).exists()
    assert not (home / LEGACY_POS_NAME).exists()

    start = resolve_outbox_start(home, outbox_size=len(body), replay=False)
    assert start == len(body)
    assert (home / POS_NAME).read_text(encoding="utf-8").strip() == str(len(body))


def test_replay_flag_starts_at_zero(tmp_path: Path):
    home = tmp_path
    body = b"PRIVMSG #bobiverse :a\nPRIVMSG #bobiverse :b\n"
    (home / "chair-outbox.txt").write_bytes(body)
    start = resolve_outbox_start(home, outbox_size=len(body), replay=True)
    assert start == 0


def test_canonical_pos_preferred_over_legacy(tmp_path: Path):
    home = tmp_path
    body = b"xxxxx"
    (home / "chair-outbox.txt").write_bytes(body)
    (home / POS_NAME).write_text("3", encoding="utf-8")
    (home / LEGACY_POS_NAME).write_text("0", encoding="utf-8")
    assert resolve_outbox_start(home, outbox_size=5) == 3


def test_pos_past_eof_clamps_no_replay(tmp_path: Path):
    home = tmp_path
    (home / "chair-outbox.txt").write_bytes(b"abc")
    write_pos(home, 999)
    assert resolve_outbox_start(home, outbox_size=3) == 3


def test_drain_migrates_legacy_and_does_not_flood(tmp_path: Path):
    home = tmp_path
    lines = [
        "PRIVMSG #bobiverse :already-sent-1",
        "PRIVMSG #bobiverse :already-sent-2",
        "PRIVMSG #bobiverse :new-only",
    ]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    (home / "chair-outbox.txt").write_bytes(raw)
    # legacy pos points before last line
    cut = raw.rfind(b"PRIVMSG")
    (home / LEGACY_POS_NAME).write_text(str(cut), encoding="utf-8")

    client = _FakeClient()
    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        client=client,
        auto_join=False,
        shops=["#flamingo"],
    )
    chair._drain_outbox()
    texts = [t for _, t in client.msgs]
    assert texts == ["new-only"]
    assert (home / POS_NAME).is_file()
    assert int((home / POS_NAME).read_text(encoding="utf-8").strip()) == len(raw)


def test_drain_cutover_no_pos_no_replay(tmp_path: Path):
    home = tmp_path
    raw = b"PRIVMSG #bobiverse :would-flood\n" * 20
    (home / "chair-outbox.txt").write_bytes(raw)
    client = _FakeClient()
    # construct seeds pos at EOF (cutover)
    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        client=client,
        auto_join=False,
    )
    assert int((home / POS_NAME).read_text(encoding="utf-8").strip()) == len(raw)
    chair._drain_outbox()
    assert client.msgs == []


def test_drain_after_seed_sends_new_lines_only(tmp_path: Path):
    """Fresh chair: seed at empty/EOF, then receiver append is announced."""
    home = tmp_path
    client = _FakeClient()
    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        client=client,
        auto_join=False,
    )
    # historic pre-seed content would already be parked; append after construct
    line = "PRIVMSG #bobiverse :GIT opened SimonBarnett/gh-Jeeves#1 x\n"
    with (home / "chair-outbox.txt").open("ab") as f:
        f.write(line.encode("utf-8"))
    chair._drain_outbox()
    assert any("GIT opened" in t for _, t in client.msgs)


def test_cli_has_replay_outbox_flag():
    from jeeves.__main__ import _parse

    args = _parse(["chair", "--replay-outbox", "--port", "6667"])
    assert args.replay_outbox is True
    args2 = _parse(["chair", "--port", "6667"])
    assert args2.replay_outbox is False


def test_empty_canonical_pos_does_not_replay(tmp_path: Path):
    """Empty chair-outbox.pos must not become offset 0 (full #bobiverse flood)."""
    home = tmp_path
    body = b"PRIVMSG #bobiverse :would-flood\n" * 30
    (home / "chair-outbox.txt").write_bytes(body)
    (home / POS_NAME).write_text("", encoding="utf-8")
    start = resolve_outbox_start(home, outbox_size=len(body), replay=False)
    assert start == len(body)
    assert int((home / POS_NAME).read_text(encoding="utf-8").strip()) == len(body)


def test_whitespace_and_garbage_pos_park_eof(tmp_path: Path):
    home = tmp_path
    body = b"PRIVMSG #bobiverse :x\n" * 5
    (home / "chair-outbox.txt").write_bytes(body)
    (home / POS_NAME).write_text("   \n", encoding="utf-8")
    assert resolve_outbox_start(home, outbox_size=len(body)) == len(body)
    (home / POS_NAME).unlink()
    (home / LEGACY_POS_NAME).write_text("not-a-number", encoding="utf-8")
    assert resolve_outbox_start(home, outbox_size=len(body)) == len(body)


def test_negative_pos_parks_eof_no_replay(tmp_path: Path):
    home = tmp_path
    (home / "chair-outbox.txt").write_bytes(b"abc")
    (home / POS_NAME).write_text("-5", encoding="utf-8")
    assert resolve_outbox_start(home, outbox_size=3, replay=False) == 3
    assert (home / POS_NAME).read_text(encoding="utf-8").strip() == "3"


def test_empty_home_seeds_zero_no_outbox_file(tmp_path: Path):
    home = tmp_path
    start = resolve_outbox_start(home, outbox_size=0, replay=False)
    assert start == 0
    assert (home / POS_NAME).read_text(encoding="utf-8").strip() == "0"


def test_resolve_reads_size_from_disk_when_omitted(tmp_path: Path):
    home = tmp_path
    body = b"PRIVMSG #bobiverse :hist\n" * 10
    (home / "chair-outbox.txt").write_bytes(body)
    start = resolve_outbox_start(home, replay=False)
    assert start == len(body)


def test_mid_line_offset_drains_remainder_only(tmp_path: Path):
    """Pos in the middle of a line: drain from that byte; no full-file replay."""
    home = tmp_path
    lines = [
        "PRIVMSG #bobiverse :sent-already",
        "PRIVMSG #bobiverse :partial-start-ok",
        "PRIVMSG #bobiverse :tail",
    ]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    (home / "chair-outbox.txt").write_bytes(raw)
    # land inside second line after first newline
    cut = raw.find(b"partial")
    assert cut > 0
    write_pos(home, cut)
    client = _FakeClient()
    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        client=client,
        auto_join=False,
    )
    chair._drain_outbox()
    texts = [t for _, t in client.msgs]
    assert "sent-already" not in texts
    assert any("partial" in t or "tail" in t for t in texts)
    assert len(texts) <= 2


def test_replay_ignored_when_canonical_pos_exists(tmp_path: Path):
    """--replay-outbox only applies when no usable pos file (CLI help contract)."""
    home = tmp_path
    body = b"PRIVMSG #bobiverse :a\nPRIVMSG #bobiverse :b\n"
    (home / "chair-outbox.txt").write_bytes(body)
    write_pos(home, len(body))
    assert resolve_outbox_start(home, outbox_size=len(body), replay=True) == len(body)
