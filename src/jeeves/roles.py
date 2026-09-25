"""Jeeves chair + bob-{machine} ear roles for G1 (scripts only)."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

from .channel_join import AutoJoinController, normalize_channel
from .flood_queue import OutboundFloodQueue
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
from .listfmt import FLOOD_S, format_unaccepted_list, list_rate_notice, list_rate_ok
from .wire import (
    is_bored,
    is_help,
    is_list,
    is_resync,
    is_status,
    is_sweep,
    parse_ack,
    parse_done,
    parse_list_filters,
    parse_nack,
    parse_sweep,
)  # noqa: F401

log = logging.getLogger("jeeves.chair")


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
        *,
        auto_join: bool = True,
        channel_denylist: list[str] | None = None,
        list_interval_s: float = 60.0,
    ):
        self.home = Path(home)
        self.report_url = report_url.rstrip("/")
        self.nick = nick
        # FR #55: shops is optional seed only (not the sole join set)
        self.shops = list(shops) if shops is not None else ["#bobiverse"]
        # FR #46: inject TlsIrcClient for Ergo; default plain G1 IrcClient
        self.client = client if client is not None else IrcClient(host, port, nick)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="jeeves-chair", daemon=True)
        self.handled: list[str] = []
        self.shop_egress: list[tuple[str, str]] = []  # (channel, text) if any slip through
        self.pm_egress: list[tuple[str, str]] = []  # (nick, text) help/list
        self.help_rate = HelpRateLimit()
        self.resync_scheduler = resync_scheduler
        self._started = time.time()
        # issue #74: pace outbound PRIVMSG on a side thread (once, not twice)
        def _send_pm(target: str, text: str) -> None:
            fn = getattr(self.client, "privmsg", None)
            if fn is None:
                return
            try:
                fn(target, text, pace=False)  # type: ignore[call-arg]
            except TypeError:
                fn(target, text)

        flood = float(getattr(self.client, "flood_s", FLOOD_S) or FLOOD_S)
        # local G1 client has no flood_s — still pace list PMs at FLOOD_S
        if not hasattr(self.client, "flood_s"):
            flood = FLOOD_S
        self._outbox = OutboundFloodQueue(_send_pm, flood_s=flood)
        # FR #52: mode grants (+h bob / +o simon) — account-trusted, no channel text
        self.mode_grants: ModeGrantController | None = None
        if hasattr(self.client, "send_raw"):
            self.mode_grants = ModeGrantController(self.client, jeeves_nick=nick, rate_s=0.05)

        self.auto_join_ctrl: AutoJoinController | None = None
        if auto_join and hasattr(self.client, "send_raw"):
            seed = list(dict.fromkeys(["#bobiverse", *[normalize_channel(s) for s in self.shops]]))
            self.auto_join_ctrl = AutoJoinController(
                self.client,
                nick=nick,
                denylist=channel_denylist or [],
                seed=seed,
                list_interval_s=list_interval_s,
            )
        elif not hasattr(self.client, "send_raw"):
            # legacy static join only
            self.client.join("#bobiverse", *self.shops)

        # Wire raw JOIN/ACCOUNT/MODE/LIST/KICK into FR52 + FR55 controllers
        if self.mode_grants is not None or self.auto_join_ctrl is not None:
            prev = getattr(self.client, "on_raw", None)

            def _on_raw(line: str) -> None:
                if prev:
                    try:
                        prev(line)
                    except Exception:
                        pass
                if self.mode_grants is not None:
                    self.mode_grants.handle_raw(line)
                if self.auto_join_ctrl is not None:
                    self.auto_join_ctrl.handle_raw(line)

            self.client.on_raw = _on_raw  # type: ignore[method-assign]
        if self.auto_join_ctrl is None and hasattr(self.client, "send_raw"):
            # auto_join disabled but client can send_raw — still static join
            if not auto_join:
                self.client.join("#bobiverse", *self.shops)
        # Policy pins — tests assert these stay false.
        assert chair_handles_bored() is False
        assert chair_may_offer() is False

    def _pm(self, nick: str, text: str) -> None:
        """Private message only (help/list). Never channel flood. Non-blocking (#74)."""
        self.pm_egress.append((nick, text))
        self._outbox.put(nick, text)

    def _shop_privmsg(self, channel: str, text: str) -> None:
        """Chair must remain silent in shops (K1). Block claim/offer egress."""
        if not shop_egress_allowed_for_chair(text) or is_forbidden_shop_egress(text):
            self.handled.append(f"blocked_shop_egress:{channel}:{text[:80]}")
            return
        self.shop_egress.append((channel, text))
        self._outbox.put(channel, text)

    def start(self) -> None:
        self._outbox.start()
        if self.resync_scheduler is not None:
            # rebuild task list on start (FR #25); scheduler owns periodic loop
            self.resync_scheduler.start(run_immediately=True)
        if self.auto_join_ctrl is not None:
            self.auto_join_ctrl.start(list_immediately=True)
            self.handled.append("autojoin_start")
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._outbox.stop()
        except Exception:
            pass
        if self.auto_join_ctrl is not None:
            try:
                self.auto_join_ctrl.stop()
            except Exception:
                pass
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
        headers = {"Content-Type": "application/json"}
        # FR #47: attach X-Bob-Secret when configured (never log value)
        try:
            from .auth_secret import load_bob_secret

            sec = load_bob_secret(homes=[self.home])
            if sec:
                headers["X-Bob-Secret"] = sec
        except Exception:
            pass
        req = urllib.request.Request(
            f"{self.report_url}/bob/v1/report",
            data=data,
            headers=headers,
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
                    self._outbox.put(target, text)
                    self.handled.append(f"announce:{text}")
            else:
                self._outbox.put("#bobiverse", line)
                self.handled.append(f"announce:{line}")
        pos_path.write_text(str(len(data)), encoding="utf-8")

    def _handle_list(self, src: str, text: str) -> None:
        """FR #50 / #208: !list from channel or PM → PM only; never channel flood."""
        if not list_rate_ok(src):
            self._pm(src, list_rate_notice(src))
            self.handled.append(f"list_rate:{src}")
            log.info("cmd=list nick=%s replies=1 rate_limited", src)
            return
        task_f, repo_f, list_all = parse_list_filters(text)
        lines = format_unaccepted_list(
            self.home, task_filter=task_f, repo_filter=repo_f, list_all=list_all
        )
        # Enqueue all lines; OutboundFloodQueue paces once (#74 — do not sleep here)
        for line in lines:
            self._pm(src, line)
        self.handled.append(f"list_pm:{src}:{len(lines)}")
        log.info("cmd=list nick=%s replies=%s", src, len(lines))

    def _handle_help(self, src: str, text: str) -> None:
        """FR #27: !help always answered by PM, never in channel."""
        ok, arg = parse_help(text)
        if not ok:
            return
        result = build_help(src, arg, rate=self.help_rate)
        for line in result.lines:
            self._pm(src, line)
        self.handled.append(f"help:{src}:{arg or '*'}:{len(result.lines)}")
        log.info("cmd=help nick=%s arg=%s replies=%s", src, arg or "*", len(result.lines))

    def _handle_status(self, src: str) -> None:
        """issue #74: !status PM snapshot (version, uptime, queue, workers)."""
        counts = queue_counts(self.home)
        q = load_queue(self.home)
        workers = q.get("workers") or {}
        busy = 0
        idle = 0
        if isinstance(workers, dict):
            for w in workers.values():
                st = str((w or {}).get("state") or "").lower() if isinstance(w, dict) else ""
                if st == "busy":
                    busy += 1
                else:
                    idle += 1
        ver = "?"
        try:
            from .versioning import version_report_payload

            ver = str(version_report_payload().get("version") or "?")
        except Exception:
            ver = "?"
        uptime = int(max(0, time.time() - self._started))
        last_resync = "n/a"
        if self.resync_scheduler is not None and getattr(self.resync_scheduler, "last", None):
            st = self.resync_scheduler.last
            last_resync = (
                f"quiet={getattr(st, 'quiet', False)} "
                f"+{getattr(st, 'added', 0)}-{getattr(st, 'removed', 0)}"
            )
        lines = [
            f"Jeeves status: version={ver} uptime_s={uptime}",
            (
                f"queue: unaccepted={counts.get('unaccepted', 0)} "
                f"accepted={counts.get('accepted', 0)} done={counts.get('done', 0)}"
            ),
            f"workers: busy={busy} idle={idle} tracked={len(workers) if isinstance(workers, dict) else 0}",
            f"last_resync: {last_resync}",
        ]
        for line in lines:
            self._pm(src, line)
        self.handled.append(f"status:{src}:{len(lines)}")
        log.info("cmd=status nick=%s replies=%s", src, len(lines))

    def _handle_resync(self, src: str) -> None:
        """issue #74 / FR #25: !resync — bob-* or simon only."""
        n = (src or "").strip().lower()
        if n != "simon" and not n.startswith("bob-"):
            self.handled.append(f"resync_denied:{src}")
            log.info("cmd=resync nick=%s denied", src)
            return
        if self.resync_scheduler is None:
            self._pm(src, "resync unavailable (no scheduler)")
            self.handled.append(f"resync_nosched:{src}")
            log.info("cmd=resync nick=%s replies=1 nosched", src)
            return
        try:
            stats = self.resync_scheduler.run_once()
            total = sum(queue_counts(self.home).values())
            body = stats.summary_line(total) if hasattr(stats, "summary_line") else "resync done"
            self._pm(src, body)
            self.handled.append(f"resync:{src}:ok")
            log.info("cmd=resync nick=%s replies=1 ok", src)
        except Exception as e:
            self._pm(src, f"resync error: {type(e).__name__}")
            self.handled.append(f"resync:{src}:err")
            log.warning("cmd=resync nick=%s err=%s", src, type(e).__name__)
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
        if is_status(text):
            self._handle_status(src)
            return
        if is_resync(text):
            self._handle_resync(src)
            return
        if is_sweep(text):
            self._handle_sweep(src, target, text)
            return
        if not target.startswith("#"):
            if is_list(text):
                self._handle_list(src, text)
            elif is_status(text):
                self._handle_status(src)
            elif is_resync(text):
                self._handle_resync(src)
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
                try:
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
                    self._post_report(
                        {
                            "op": "worker_state",
                            "nick": src,
                            "state": "busy",
                            "job": job,
                        }
                    )
                except Exception as e:
                    self.handled.append(f"ack_report_err:{src}")
                    log.warning("cmd=ack nick=%s report_err=%s", src, type(e).__name__)
                self.handled.append(f"ack:{src}:{ack.repo}#{ack.number}")
                log.info("cmd=ack nick=%s repo=%s#%s", src, ack.repo, ack.number)
            else:
                self.handled.append(f"ack_no_match:{src}:{ack.repo}#{ack.number}")
            return
        nack = parse_nack(text)
        if nack:
            kind, task, repo, number = nack
            st, row = nack_job(self.home, src, task, repo, number)
            self._post_report({"op": "worker_state", "nick": src, "state": "idle"})
            self.handled.append(f"nack:{src}:{repo}#{number}:{st}")
            log.info("cmd=nack nick=%s repo=%s#%s", src, repo, number)
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
            log.info("cmd=done nick=%s repo=%s#%s", src, done.repo, done.number)
            return

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_outbox()
            except Exception as e:
                log.warning("drain_outbox_err=%s", type(e).__name__)
            try:
                msg = self.client.wait_privmsg(timeout=0.3)
            except OSError:
                # FR #46: native TLS client reconnect-in-place (backoff on throttle)
                recon = getattr(self.client, "reconnect", None)
                if callable(recon):
                    try:
                        recon()
                        if self.auto_join_ctrl is not None:
                            self.auto_join_ctrl.note_reconnect()
                        else:
                            self.client.join("#bobiverse", *self.shops)
                    except Exception:
                        time.sleep(1.0)
                msg = None
            if msg:
                src, target, text = msg
                try:
                    self._handle_shop(src, target, text)
                except Exception as e:
                    self.handled.append(f"handler_err:{src}:{type(e).__name__}")
                    log.exception(
                        "handler_err nick=%s target=%s err=%s",
                        src,
                        target,
                        type(e).__name__,
                    )
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
