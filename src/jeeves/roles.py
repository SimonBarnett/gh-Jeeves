"""Jeeves chair + bob-{machine} ear roles for G1 (scripts only)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

from .local_ircd import IrcClient
from .nicks import bored_gate, canonical_worker_nick, worker_shop_channel
from .queue import (
    accept_job,
    complete_job,
    format_offer,
    load_queue,
    top_unaccepted,
    worker_state,
)
from .cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    is_forbidden_shop_egress,
    shop_egress_allowed_for_chair,
)
from .wire import is_bored, is_list, parse_ack, parse_done


class JeevesChair:
    """
    Drains chair-outbox to #bobiverse; silent ACK/DONE listener in shops.
    Never handles !bored / never offers (K1 CAST IRON).
    Optional FR #25 resync scheduler (GitHub rebuild) injected by caller.
    """

    def __init__(
        self,
        host: str,
        port: int,
        home: Path,
        report_url: str,
        nick: str = "Jeeves",
        shops: list[str] | None = None,
        resync_scheduler=None,
    ):
        self.home = Path(home)
        self.report_url = report_url.rstrip("/")
        self.nick = nick
        self.shops = shops or ["#flamingo"]
        self.client = IrcClient(host, port, nick)
        self.client.join("#bobiverse", *self.shops)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="jeeves-chair", daemon=True)
        self.handled: list[str] = []
        self.shop_egress: list[tuple[str, str]] = []  # (channel, text) if any slip through
        self.resync_scheduler = resync_scheduler
        # Policy pins — tests assert these stay false.
        assert chair_handles_bored() is False
        assert chair_may_offer() is False

    def _shop_privmsg(self, channel: str, text: str) -> None:
        """Chair must remain silent in shops (K1). Block claim/offer egress."""
        if not shop_egress_allowed_for_chair(text) or is_forbidden_shop_egress(text):
            self.handled.append(f"blocked_shop_egress:{channel}:{text[:80]}")
            return
        self.shop_egress.append((channel, text))
        self.client.privmsg(channel, text)

    def start(self) -> None:
        if self.resync_scheduler is not None:
            # rebuild task list on start (FR #25); scheduler owns periodic loop
            self.resync_scheduler.start(run_immediately=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self.resync_scheduler is not None:
            try:
                self.resync_scheduler.stop()
            except Exception:
                pass
        self.client.close()
        if self._t.is_alive() or self._t.ident is not None:
            try:
                self._t.join(timeout=2.0)
            except RuntimeError:
                pass

    def _post_report(self, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.report_url}/bob/v1/report",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    def _drain_outbox(self) -> None:
        path = self.home / "chair-outbox.txt"
        pos_path = self.home / "chair-outbox.pos"
        if not path.is_file():
            return
        pos = 0
        if pos_path.is_file():
            try:
                pos = int(pos_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                pos = 0
        data = path.read_bytes()
        if pos > len(data):
            pos = 0
        chunk = data[pos:].decode("utf-8", errors="replace")
        if not chunk:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.upper().startswith("PRIVMSG "):
                # PRIVMSG #bobiverse :text
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    target = parts[1]
                    text = parts[2][1:] if parts[2].startswith(":") else parts[2]
                    self.client.privmsg(target, text)
                    self.handled.append(f"announce:{text}")
            else:
                self.client.privmsg("#bobiverse", line)
                self.handled.append(f"announce:{line}")
        pos_path.write_text(str(len(data)), encoding="utf-8")

    def _handle_shop(self, src: str, target: str, text: str) -> None:
        if not target.startswith("#"):
            if is_list(text):
                q = load_queue(self.home)
                rows = q.get("unaccepted") or []
                if not rows:
                    self.client.privmsg(src, "queue empty")
                else:
                    for row in rows[:10]:
                        self.client.privmsg(
                            src,
                            f"{row.get('task')} {row.get('repo')}{row.get('id')} {row.get('line') or ''}".strip(),
                        )
                self.handled.append("list")
            return
        # silent shop: ACK / DONE only — never !bored, never OFFER/claim (K1)
        if is_bored(text):
            # K1: ear owns idle pings; chair never claims or replies in shop.
            self.handled.append("ignored_bored")
            return
        ack = parse_ack(text)
        if ack:
            # K2: only real worker nicks may ACK in their own shop
            if bored_gate(src, target) != "ok":
                self.handled.append(f"ignored_ack_bad_nick:{src}")
                return
            st, row = accept_job(self.home, src, target, ack.task, ack.repo, ack.number)
            if st == "accepted":
                self._post_report(
                    {
                        "op": "worker_state",
                        "nick": src,
                        "state": "busy",
                        "job": f"{ack.repo} {ack.task} #{ack.number}",
                    }
                )
                self.handled.append(f"ack:{src}:{ack.repo}#{ack.number}")
            return
        done = parse_done(text)
        if done:
            if bored_gate(src, target) != "ok":
                self.handled.append(f"ignored_done_bad_nick:{src}")
                return
            st, row = complete_job(
                self.home, src, done.task, done.repo, done.number, done.result, done.url
            )
            self._post_report({"op": "worker_state", "nick": src, "state": "idle"})
            self.handled.append(f"done:{src}:{done.repo}#{done.number}:{st}")
            return

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_outbox()
            except Exception:
                pass
            msg = self.client.wait_privmsg(timeout=0.3)
            if msg:
                src, target, text = msg
                try:
                    self._handle_shop(src, target, text)
                except Exception:
                    pass
            time.sleep(0.05)


class BobEar:
    """bob-{machine} ear: owns !bored → OFFER top unaccepted; never Jeeves."""

    def __init__(
        self,
        host: str,
        port: int,
        home: Path,
        machine: str = "flamingo",
        nick: str | None = None,
    ):
        self.home = Path(home)
        self.machine = machine.lower()
        self.nick = nick or f"bob-{self.machine}"
        self.shop = f"#{self.machine}"
        self.client = IrcClient(host, port, self.nick)
        self.client.join("#bobiverse", self.shop)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="bob-ear", daemon=True)
        self.offers: list[str] = []
        self.open_offer: dict[str, dict] = {}  # nick -> row

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self.client.close()
        self._t.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self.client.wait_privmsg(timeout=0.3)
            if not msg:
                continue
            src, target, text = msg
            if target.lower() != self.shop:
                continue
            if not is_bored(text):
                continue
            # K2: accept {machine}-{pid} (and legacy w-*); reject bob-/Jeeves
            gate = bored_gate(src, target)
            if gate != "ok":
                continue
            canon = canonical_worker_nick(src)
            if not canon:
                continue
            shop = worker_shop_channel(src)
            if shop != self.shop:
                continue
            if worker_state(self.home, src) == "busy":
                self.client.privmsg(self.shop, f"{src}: NAK busy")
                continue
            if src in self.open_offer:
                # one open offer per worker
                continue
            row = top_unaccepted(self.home)
            if not row:
                self.client.privmsg(self.shop, f"{src}: no jobs")
                continue
            line = format_offer(src, row)
            self.client.privmsg(self.shop, line)
            self.offers.append(line)
            self.open_offer[src] = row
