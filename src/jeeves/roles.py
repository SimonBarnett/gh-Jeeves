"""Jeeves chair + bob-{machine} ear roles for G1 (scripts only)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

from .local_ircd import IrcClient
from .mode_grants import ModeGrantController
from .nicks import bored_gate, canonical_worker_nick, worker_shop_channel
from .offer import EarOfferState, contains_assign, is_single_line
from .queue import (
    accept_job,
    complete_job,
    load_queue,
    nack_job,
    queue_counts,
    worker_state,
)
from .cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    is_forbidden_shop_egress,
    shop_egress_allowed_for_chair,
)
from .helpcmd import HelpRateLimit, build_help, parse_help
from .listfmt import format_unaccepted_list, list_rate_notice, list_rate_ok
from .wire import (
    is_bored,
    is_help,
    is_list,
    is_sweep,
    parse_ack,
    parse_done,
    parse_list_filters,
    parse_nack,
    parse_sweep,
)  # noqa: F401


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
        client=None,
    ):
        self.home = Path(home)
        self.report_url = report_url.rstrip("/")
        self.nick = nick
        self.shops = shops or ["#flamingo"]
        # FR #46: inject TlsIrcClient for Ergo; default plain G1 IrcClient
        self.client = client if client is not None else IrcClient(host, port, nick)
        self.client.join("#bobiverse", *self.shops)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="jeeves-chair", daemon=True)
        self.handled: list[str] = []
        self.shop_egress: list[tuple[str, str]] = []  # (channel, text) if any slip through
        self.pm_egress: list[tuple[str, str]] = []  # (nick, text) help/list
        self.help_rate = HelpRateLimit()
        self.resync_scheduler = resync_scheduler
        # FR #52: mode grants (+h bob / +o simon) — account-trusted, no channel text
        self.mode_grants: ModeGrantController | None = None
        if hasattr(self.client, "send_raw"):
            self.mode_grants = ModeGrantController(self.client, jeeves_nick=nick, rate_s=0.05)
            prev = getattr(self.client, "on_raw", None)

            def _on_raw(line: str) -> None:
                if prev:
                    try:
                        prev(line)
                    except Exception:
                        pass
                if self.mode_grants is not None:
                    self.mode_grants.handle_raw(line)

            self.client.on_raw = _on_raw  # type: ignore[method-assign]
        # Policy pins — tests assert these stay false.
        assert chair_handles_bored() is False
        assert chair_may_offer() is False

    def _pm(self, nick: str, text: str) -> None:
        """Private message only (help/list). Never channel flood."""
        self.pm_egress.append((nick, text))
        self.client.privmsg(nick, text)

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

    def _handle_list(self, src: str, text: str) -> None:
        """FR #39 / #208: !list by PM only (channel or PM)."""
        if not list_rate_ok(src):
            self._pm(src, list_rate_notice(src))
            self.handled.append(f"list_rate:{src}")
            return
        task_f, repo_f, list_all = parse_list_filters(text)
        lines = format_unaccepted_list(
            self.home, task_filter=task_f, repo_filter=repo_f, list_all=list_all
        )
        for line in lines:
            self._pm(src, line)
        self.handled.append(f"list_pm:{src}:{len(lines)}")

    def _handle_help(self, src: str, text: str) -> None:
        """FR #27: !help always answered by PM, never in channel."""
        ok, arg = parse_help(text)
        if not ok:
            return
        result = build_help(src, arg, rate=self.help_rate)
        for line in result.lines:
            self._pm(src, line)
        self.handled.append(f"help:{src}:{arg or '*'}:{len(result.lines)}")

    def _handle_sweep(self, src: str, target: str, text: str) -> None:
        """FR #52: !sweep [channel] — simon + account simon only; no channel text."""
        ch = parse_sweep(text)
        if ch is None:
            return
        if (src or "").strip().lower() != "simon":
            self.handled.append(f"sweep_denied_nick:{src}")
            return
        if self.mode_grants is None:
            self.handled.append("sweep_no_controller")
            return
        acct = (self.mode_grants.state.accounts.get("simon") or "").strip().lower()
        if acct != "simon":
            self.handled.append("sweep_denied_unauth")
            self.mode_grants.state.events.append(f"sweep_denied_unauth:{src}")
            return
        if not ch:
            ch = target if target.startswith("#") else "#bobiverse"
        # Prefer NAMES-driven resweep; also grant known accounts immediately.
        try:
            self.client.send_raw(f"NAMES {ch}")
        except Exception:
            pass
        known = list(self.mode_grants.state.accounts.keys())
        granted = self.mode_grants.sweep_channel(ch, known)
        self.handled.append(f"sweep:{ch}:{len(granted)}")

    def _handle_shop(self, src: str, target: str, text: str) -> None:
        # !help from channel or PM → reply by PM only (no channel flood)
        if is_help(text) or parse_help(text)[0]:
            self._handle_help(src, text)
            return
        if is_list(text):
            self._handle_list(src, text)
            return
        if is_sweep(text):
            self._handle_sweep(src, target, text)
            return
        if not target.startswith("#"):
            if is_list(text):
                self._handle_list(src, text)
            return
        # silent shop: ACK / DONE only — never !bored, never OFFER/claim (K1)
        if is_bored(text):
            # K1: ear owns idle pings; chair never claims or replies in shop.
            self.handled.append("ignored_bored")
            return
        ack = parse_ack(text)
        if ack:
            # K2: only real worker nicks may ACK in their own shop
            if bored_gate(self.home, src, target, skip_idle_check=True) != "ok":
                self.handled.append(f"ignored_ack_bad_nick:{src}")
                return
            # K3 / FR #4: mark accepted + busy on webhook (never leave accepted empty after ACK).
            st, row = accept_job(self.home, src, target, ack.task, ack.repo, ack.number)
            counts = queue_counts(self.home)
            if st == "accepted":
                job = f"{ack.repo} {ack.task} #{ack.number}"
                self._post_report(
                    {
                        "op": "queue_accept",
                        "nick": src,
                        "state": "busy",
                        "job": job,
                        "repo": ack.repo,
                        "task": ack.task,
                        "id": f"#{ack.number}",
                        "accepted_row": row,
                        "queue": counts,
                    }
                )
                # also legacy worker_state for older digest consumers
                self._post_report(
                    {
                        "op": "worker_state",
                        "nick": src,
                        "state": "busy",
                        "job": job,
                    }
                )
                self.handled.append(f"ack:{src}:{ack.repo}#{ack.number}")
            else:
                self.handled.append(f"ack_no_match:{src}:{ack.repo}#{ack.number}")
            return
        nack = parse_nack(text)
        if nack:
            kind, task, repo, number = nack
            st, row = nack_job(self.home, src, task, repo, number)
            self._post_report({"op": "worker_state", "nick": src, "state": "idle"})
            self.handled.append(f"nack:{src}:{repo}#{number}:{st}")
            return
        done = parse_done(text)
        if done:
            if bored_gate(self.home, src, target, skip_idle_check=True) != "ok":
                self.handled.append(f"ignored_done_bad_nick:{src}")
                return
            st, row = complete_job(
                self.home, src, done.task, done.repo, done.number, done.result, done.url
            )
            counts = queue_counts(self.home)
            self._post_report(
                {
                    "op": "queue_done",
                    "nick": src,
                    "state": "idle",
                    "repo": done.repo,
                    "task": done.task,
                    "id": f"#{done.number}",
                    "result": done.result,
                    "queue": counts,
                }
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
            try:
                msg = self.client.wait_privmsg(timeout=0.3)
            except OSError:
                # FR #46: native TLS client reconnect-in-place (backoff on throttle)
                recon = getattr(self.client, "reconnect", None)
                if callable(recon):
                    try:
                        recon()
                        self.client.join("#bobiverse", *self.shops)
                    except Exception:
                        time.sleep(1.0)
                msg = None
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
        self.offer_state = EarOfferState()
        # back-compat alias for tests that read open_offer
        self.open_offer = self.offer_state.open

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self.client.close()
        self._t.join(timeout=2.0)

    def _emit_offer_line(self, line: str) -> None:
        """K11: only single-line OFFER; never multi-line ASSIGN."""
        if contains_assign(line) or not is_single_line(line):
            self.offers.append(f"blocked_bad_line:{line[:40]}")
            return
        self.client.privmsg(self.shop, line)
        self.offers.append(line)

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self.client.wait_privmsg(timeout=0.3)
            if not msg:
                continue
            src, target, text = msg
            if target.lower() != self.shop:
                continue
            # Clear open offer on ACK/DONE so worker can take another job later
            if parse_ack(text) and bored_gate(self.home, src, target, skip_idle_check=True) == "ok":
                self.offer_state.on_worker_ack(src)
                continue
            if parse_done(text) and bored_gate(self.home, src, target, skip_idle_check=True) == "ok":
                self.offer_state.on_worker_done(src)
                continue
            if not is_bored(text):
                continue
            # K2: accept {machine}-{pid} (and legacy w-*); reject bob-/Jeeves
            gate = bored_gate(self.home, src, target, skip_idle_check=True)
            if gate != "ok":
                continue
            canon = canonical_worker_nick(src)
            if not canon:
                continue
            shop = worker_shop_channel(src)
            if shop != self.shop:
                continue
            # K11: one single-line OFFER per worker, busy-gated; ear owns offer (not ASSIGN)
            decision = self.offer_state.decide(self.home, src, machine=self.machine)
            if decision.action == "offer" and decision.line:
                self._emit_offer_line(decision.line)
            elif decision.action == "nak_busy" and decision.line:
                self._emit_offer_line(decision.line)
            elif decision.action == "no_jobs" and decision.line:
                self._emit_offer_line(decision.line)
            elif decision.action == "nak_open":
                # silent: already have one open offer — do not stack multi-line wakes
                self.offers.append(f"skip:one_open:{src}")
            else:
                self.offers.append(f"skip:{decision.action}:{src}")
