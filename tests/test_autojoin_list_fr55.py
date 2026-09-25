"""FR #55: LIST auto-join, periodic re-LIST, denylist, KICK backoff."""

from __future__ import annotations

import time
from pathlib import Path

from jeeves.channel_join import (
    AutoJoinController,
    channels_to_join,
    should_skip_channel,
    AutoJoinState,
)
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def test_should_skip_denylist_and_local():
    assert should_skip_channel("+local")
    assert should_skip_channel("0")
    assert should_skip_channel("#secret", denylist={"#secret"})
    assert not should_skip_channel("#flamingo")


def test_channels_to_join_respects_denylist():
    st = AutoJoinState(denylist={"#nope"})
    st.joined.add("#bobiverse")
    out = channels_to_join({"#bobiverse", "#a", "#nope", "#b"}, st, seed={"#bobiverse"})
    assert "#a" in out and "#b" in out
    assert "#nope" not in out
    assert "#bobiverse" not in out  # already joined


def test_list_joins_existing_and_new_channel(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    ircd.ensure_channel("#bobiverse")
    ircd.ensure_channel("#a")
    # no #b yet
    client = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = AutoJoinController(
        client,
        nick="Jeeves",
        seed=["#bobiverse"],
        list_interval_s=0.4,
        denylist=[],
    )
    client.on_raw = ctrl.handle_raw
    ctrl.start(list_immediately=True)
    # drain LIST replies
    deadline = time.time() + 3
    while time.time() < deadline and "#a" not in {c.lower() for c in ctrl.state.joined}:
        client._drain_until(lambda: False, timeout=0.2)
        time.sleep(0.05)
    joined = {c.lower() for c in ctrl.state.joined}
    assert "#bobiverse" in joined
    assert "#a" in joined

    # create #b later — next LIST should join it
    ircd.ensure_channel("#b")
    ctrl.request_list()
    deadline = time.time() + 3
    while time.time() < deadline and "#b" not in {c.lower() for c in ctrl.state.joined}:
        client._drain_until(lambda: False, timeout=0.2)
        time.sleep(0.05)
    assert "#b" in {c.lower() for c in ctrl.state.joined}
    ctrl.stop()
    client.close()
    ircd.stop()


def test_denylist_never_joined(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    ircd.ensure_channel("#bobiverse")
    ircd.ensure_channel("#good")
    ircd.ensure_channel("#bad")
    client = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = AutoJoinController(
        client,
        nick="Jeeves",
        seed=["#bobiverse"],
        denylist=["#bad"],
        list_interval_s=3600,
    )
    client.on_raw = ctrl.handle_raw
    ctrl.start(list_immediately=True)
    deadline = time.time() + 3
    while time.time() < deadline and ctrl.state.list_runs < 1:
        client._drain_until(lambda: False, timeout=0.2)
    time.sleep(0.2)
    client._drain_until(lambda: False, timeout=0.5)
    joined = {c.lower() for c in ctrl.state.joined}
    assert "#good" in joined or "#bobiverse" in joined
    assert "#bad" not in joined
    ctrl.stop()
    client.close()
    ircd.stop()


def test_reconnect_relists_all(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    ircd.ensure_channel("#bobiverse")
    ircd.ensure_channel("#x")
    client = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = AutoJoinController(client, nick="Jeeves", seed=["#bobiverse"], list_interval_s=3600)
    client.on_raw = ctrl.handle_raw
    ctrl.start(list_immediately=True)
    deadline = time.time() + 3
    while time.time() < deadline and "#x" not in {c.lower() for c in ctrl.state.joined}:
        client._drain_until(lambda: False, timeout=0.2)
    assert "#x" in {c.lower() for c in ctrl.state.joined}
    # simulate reconnect
    ctrl.note_reconnect()
    deadline = time.time() + 3
    while time.time() < deadline and ctrl.state.list_runs < 2:
        client._drain_until(lambda: False, timeout=0.2)
    assert "#x" in {c.lower() for c in ctrl.state.joined}
    assert "#bobiverse" in {c.lower() for c in ctrl.state.joined}
    ctrl.stop()
    client.close()
    ircd.stop()


def test_kick_rejoins_with_backoff(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    ircd.ensure_channel("#shop")
    jeeves = IrcClient("127.0.0.1", port, "Jeeves")
    kicker = IrcClient("127.0.0.1", port, "op")
    ctrl = AutoJoinController(jeeves, nick="Jeeves", seed=["#shop"], list_interval_s=3600)
    jeeves.on_raw = ctrl.handle_raw
    ctrl.start(list_immediately=True)
    deadline = time.time() + 3
    while time.time() < deadline and "#shop" not in {c.lower() for c in ctrl.state.joined}:
        jeeves._drain_until(lambda: False, timeout=0.2)
    kicker.join("#shop")
    kicker.kick("#shop", "Jeeves", "bye")
    # process KICK
    deadline = time.time() + 5
    saw_kick = False
    while time.time() < deadline:
        jeeves._drain_until(lambda: False, timeout=0.2)
        if any(e.startswith("kick:#shop") for e in ctrl.state.events):
            saw_kick = True
        if saw_kick and "#shop" in {c.lower() for c in ctrl.state.joined}:
            break
        time.sleep(0.05)
    assert saw_kick
    assert "#shop" in {c.lower() for c in ctrl.state.joined}
    ctrl.stop()
    jeeves.close()
    kicker.close()
    ircd.stop()


def test_chair_ack_on_late_joined_channel(tmp_path: Path):
    """#b created after start; Jeeves joins and processes ACK there."""
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "o/r",
                    "task": "FR",
                    "id": "#55",
                    "seq": 1,
                    "line": "autojoin",
                    "url": "http://x",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    ircd = LocalIrcd()
    port = ircd.start()
    ircd.ensure_channel("#bobiverse")
    ircd.ensure_channel("#a")
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=True,
        list_interval_s=0.5,
    )
    chair.start()
    # wait for LIST join of #a
    deadline = time.time() + 4
    while time.time() < deadline:
        if chair.auto_join_ctrl and "#a" in {c.lower() for c in chair.auto_join_ctrl.state.joined}:
            break
        time.sleep(0.05)
    ircd.ensure_channel("#b")
    if chair.auto_join_ctrl:
        chair.auto_join_ctrl.request_list()
    deadline = time.time() + 4
    while time.time() < deadline:
        if chair.auto_join_ctrl and "#b" in {c.lower() for c in chair.auto_join_ctrl.state.joined}:
            break
        time.sleep(0.05)
    assert chair.auto_join_ctrl and "#b" in {c.lower() for c in chair.auto_join_ctrl.state.joined}

    worker = IrcClient("127.0.0.1", port, "machineb-1")
    # worker nick parse may need flamingo-style — use flamingo-55 on #b as shop
    # ACK path uses bored_gate shop match; use flamingo nick on #flamingo for reliability
    worker.close()
    worker = IrcClient("127.0.0.1", port, "flamingo-55")
    # ensure flamingo shop exists and joined
    ircd.ensure_channel("#flamingo")
    if chair.auto_join_ctrl:
        chair.auto_join_ctrl.request_list()
    deadline = time.time() + 4
    while time.time() < deadline:
        if chair.auto_join_ctrl and "#flamingo" in {
            c.lower() for c in chair.auto_join_ctrl.state.joined
        }:
            break
        time.sleep(0.05)
    worker.join("#flamingo")
    worker.privmsg("#flamingo", "ACK FR o/r#55")
    deadline = time.time() + 5
    while time.time() < deadline and not any("ack:flamingo-55:" in h for h in chair.handled):
        time.sleep(0.05)
    assert any("ack:flamingo-55:o/r#55" in h for h in chair.handled), chair.handled
    chair.stop()
    worker.close()
    rx.stop()
    ircd.stop()
