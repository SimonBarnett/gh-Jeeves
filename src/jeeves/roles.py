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
from .assign import (
    ChairAssignState,
    live_seats_from_modes,
    trust_bored,
)
from .offer import EarOfferState, contains_assign, is_single_line
from .queue import (
    accept_job,
    complete_job,
    expire_stale_workers,
    load_queue,
    nack_job,
    queue_counts,
    release_worker,
    worker_state,
)
from .cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    is_forbidden_shop_egress,
    shop_egress_allowed_for_chair,
)
from .outbox_pos import outbox_path, resolve_outbox_start, write_pos
from .helpcmd import HelpRateLimit, build_help, parse_help
from .focus import (
    handle_focus_cmd,
    handle_unfocus_cmd,
    may_mutate_focus,
    parse_focus_cmd,
    parse_unfocus_cmd,
)
from .ignore import (
    format_ignored_lines,
    handle_ignore_add,
    handle_unignore,
    is_ignored_cmd,
    may_mutate_ignore,
    parse_ignore_cmd,
    parse_unignore_cmd,
)
from .listfmt import FLOOD_S, format_unaccepted_list, list_rate_notice, list_rate_ok
from .wire import (
    ack_format_hint,
    is_bored,
    is_focus,
    is_help,
    is_ignore,
    is_ignored_list,
    is_list,
    is_resync,
    is_status,
    is_sweep,
    is_unfocus,
    is_unignore,
    looks_like_ack,
    parse_ack,
    parse_done,
    parse_list_filters,
    parse_nack,
    parse_sweep,
)  # noqa: F401

log = logging.getLogger("jeeves.chair")


def _log_announce(logger: logging.Logger, text: str) -> None:
    """FR #73: event=announce with repo#n and mode when parseable."""
    import re

    m = re.search(
        r"(?i)\b(opened|closed|merged|reopened|synchronize|ready_for_review)?\s*"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b",
        text or "",
    )
    if m:
        mode = (m.group(1) or "git").lower()
        logger.info(
            "event=announce repo=%s#%s mode=%s",
            m.group(2),
            m.group(3),
            mode,
        )
    else:
        # still one line; no full body dump
        snippet = (text or "")[:80].replace("\n", " ")
        logger.info("event=announce mode=other text=%s", snippet)

# FR #73: announce line → event=announce repo=#n mode=...
_ANNOUNCE_REPO = __import__("re").compile(
    r"(?i)\b((?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+)#(\d+)\b"
)
_ANNOUNCE_MODE = __import__("re").compile(r"\b(FR|MRB|UAT|PR)\b", __import__("re").I)


def _log_announce_line(text: str) -> None:
    """One INFO per announce; never log full body if it might hold secrets."""
    m = _ANNOUNCE_REPO.search(text or "")
    mode_m = _ANNOUNCE_MODE.search(text or "")
    repo = m.group(1) if m else "?"
    num = m.group(2) if m else "?"
    mode = mode_m.group(1).upper() if mode_m else "GIT"
    log.info("event=announce repo=%s#%s mode=%s", repo, num, mode)


class JeevesChair:
    """
    Drains chair-outbox to #bobiverse; ACK/DONE listener in shops.
    FR #106 CAST IRON: assigns next job on worker !bored in #{machine}.
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
        replay_outbox: bool = False,
    ):
        self.home = Path(home)
        self.report_url = report_url.rstrip("/")
        self.nick = nick
        # FR #71: default never replay historical chair-outbox on cutover
        self.replay_outbox = bool(replay_outbox)
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

        # FR #71: seed pos once at start — migrate legacy or park at EOF so history
        # is not replayed; lines appended after this are drained normally.
        try:
            ob = outbox_path(self.home)
            size = ob.stat().st_size if ob.is_file() else 0
            resolve_outbox_start(self.home, outbox_size=size, replay=self.replay_outbox)
        except OSError:
            pass

        # Wire raw JOIN/ACCOUNT/MODE/LIST/KICK/QUIT into FR52 + FR55 + FR #102
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
            self._handle_quit_raw(line)

        self.client.on_raw = _on_raw  # type: ignore[method-assign]
        if self.auto_join_ctrl is None and hasattr(self.client, "send_raw"):
            # auto_join disabled but client can send_raw — still static join
            if not auto_join:
                self.client.join("#bobiverse", *self.shops)
        # Policy pins — FR #106: chair owns !bored → assign.
        assert chair_handles_bored() is True
        assert chair_may_offer() is True
        self._last_stale_sweep = 0.0
        self.assign_state = ChairAssignState()
        self.assign_state.bind(self.home)
        # Optional inject for tests: set of live worker nicks
        self.live_seats_override: set[str] | None = None

    def _pm(self, nick: str, text: str) -> None:
        """Private message only (help/list). Never channel flood. Non-blocking (#74)."""
        self.pm_egress.append((nick, text))
        self._outbox.put(nick, text)

    def _shop_privmsg(self, channel: str, text: str) -> None:
        """FR #106: assign / nothing-queued lines only; block legacy OFFER/claim."""
        if not shop_egress_allowed_for_chair(text) or is_forbidden_shop_egress(text):
            self.handled.append(f"blocked_shop_egress:{channel}:{text[:80]}")
            return
        self.shop_egress.append((channel, text))
        self._outbox.put(channel, text)

    def _live_seats(self, shop: str) -> set[str]:
        if self.live_seats_override is not None:
            return set(self.live_seats_override)
        modes = None
        if self.mode_grants is not None:
            modes = self.mode_grants.state.modes
        return live_seats_from_modes(modes, shop=shop)

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
        # FR #71: chair-outbox.pos (migrate legacy .txt.pos; no replay unless flag)
        path = outbox_path(self.home)
        if not path.is_file():
            return
        data = path.read_bytes()
        pos = resolve_outbox_start(
            self.home, outbox_size=len(data), replay=self.replay_outbox
        )
        chunk = data[pos:].decode("utf-8", errors="replace")
        if not chunk:
            write_pos(self.home, len(data))
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
                    # FR #73: one INFO per announce (repo#n + mode if present)
                    _log_announce_line(text)
            else:
                self._outbox.put("#bobiverse", line)
                self.handled.append(f"announce:{line}")
                _log_announce_line(line)
        write_pos(self.home, len(data))

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
        log.info(
            "event=cmd name=list nick=%s replies=%s",
            src,
            len(lines),
        )
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
        """FR #52 / #107: !sweep [channel] — owner nick + owner account; no channel text."""
        from .focus import owner_account_name

        ch = parse_sweep(text)
        if ch is None:
            return
        owner = owner_account_name()
        src_l = (src or "").strip().lower()
        if src_l != owner and not src_l.startswith(f"{owner}-"):
            self.handled.append(f"sweep_denied_nick:{src}")
            return
        if self.mode_grants is None:
            self.handled.append("sweep_no_controller")
            return
        acct = (self._account_for_nick(src) or "").strip().lower()
        if acct != owner:
            self.handled.append("sweep_denied_unauth")
            self.handled.append(f"sweep_denied_unauth:{src}:account={acct or 'none'}")
            self.mode_grants.state.events.append(f"sweep_denied_unauth:{src}")
            log.info(
                "cmd=sweep nick=%s denied account=%s live=True",
                src,
                acct or "none",
            )
            return
        if not ch:
            ch = target if target.startswith("#") else "#bobiverse"
        try:
            self.client.send_raw(f"NAMES {ch}")
            # FR #107: refresh accounts for nicks already present
            self.mode_grants.request_who(ch)
        except Exception:
            pass
        known = list(self.mode_grants.state.accounts.keys())
        granted = self.mode_grants.sweep_channel(ch, known)
        self.handled.append(f"sweep:{ch}:{len(granted)}")

    def _account_for_nick(self, nick: str) -> str | None:
        """FR #107: services account for *this* nick (not a hard-coded simon key)."""
        if self.mode_grants is None:
            return None
        n = (nick or "").strip().lower()
        if not n:
            return None
        return (self.mode_grants.state.accounts.get(n) or "").strip() or None

    def _simon_account(self) -> str | None:
        """Deprecated alias — prefer _account_for_nick(sender)."""
        from .focus import owner_account_name

        return self._account_for_nick(owner_account_name())

    def _ops_account_for(self, nick: str) -> str | None:
        from .focus import owner_account_name

        n = (nick or "").strip().lower()
        if self.mode_grants is None:
            return None
        owner = owner_account_name()
        if n == owner or n.startswith(f"{owner}-"):
            return self._account_for_nick(nick)
        # bob-* ops: nick prefix is enough (fleet ear)
        if n.startswith("bob-"):
            return n
        return self._account_for_nick(nick)

    def _handle_ignore_cmds(self, src: str, text: str) -> bool:
        """FR #75: !ignore / !unignore / !ignored — PM replies only."""
        if is_ignored_list(text) or is_ignored_cmd(text):
            for line in format_ignored_lines(self.home):
                self._pm(src, line)
            self.handled.append(f"ignored_list:{src}")
            return True
        ign = parse_ignore_cmd(text)
        if ign is not None or is_ignore(text):
            if ign is None:
                ign = ""
            if not self._ignore_mutator_ok(src):
                self._pm(src, "ignore: denied (simon or bob-* ops only)")
                self.handled.append(f"ignore_denied:{src}")
                return True
            if not (ign or "").strip():
                self._pm(src, "ignore: usage !ignore {repo}")
                self.handled.append(f"ignore_usage:{src}")
                return True
            for line in handle_ignore_add(self.home, ign):
                self._pm(src, line)
            self.handled.append(f"ignore:{src}:{ign}")
            return True
        un = parse_unignore_cmd(text)
        if un is not None or is_unignore(text):
            if un is None:
                un = ""
            if not self._ignore_mutator_ok(src):
                self._pm(src, "unignore: denied (simon or bob-* ops only)")
                self.handled.append(f"unignore_denied:{src}")
                return True
            if not (un or "").strip():
                self._pm(src, "unignore: usage !unignore {repo}")
                self.handled.append(f"unignore_usage:{src}")
                return True
            for line in handle_unignore(self.home, un):
                self._pm(src, line)
            self.handled.append(f"unignore:{src}:{un}")
            return True
        return False

    def _ignore_mutator_ok(self, src: str) -> bool:
        """Owner needs services account when mode_grants is live; bob-* is ops by nick."""
        from .focus import owner_account_name

        src_l = (src or "").strip().lower()
        if src_l.startswith("bob-"):
            return True
        owner = owner_account_name()
        if src_l == owner or src_l.startswith(f"{owner}-"):
            if self.mode_grants is None:
                return may_mutate_ignore(src, account=None)
            acct = (self._account_for_nick(src) or "").strip().lower()
            return acct == owner
        return False

    def _focus_mutator_ok(self, src: str) -> bool:
        """FR #68 / #107: owner nick; services account keyed by *sender* nick."""
        live = self.mode_grants is not None
        acct = self._account_for_nick(src) if live else None
        return may_mutate_focus(src, account=acct, mode_grants_live=live)

    def _handle_focus_cmds(self, src: str, text: str) -> bool:
        """FR #68: !focus / !unfocus — PM only."""
        if is_focus(text) or parse_focus_cmd(text) is not None:
            arg = parse_focus_cmd(text)
            if arg is None:
                arg = ""
            if not self._focus_mutator_ok(src):
                live = self.mode_grants is not None
                acct = self._account_for_nick(src) if live else None
                self._pm(src, "focus: denied (owner account required)")
                self.handled.append(f"focus_denied:{src}")
                log.info(
                    "cmd=focus nick=%s denied account=%s live=%s",
                    src,
                    (acct or "none"),
                    live,
                )
                return True
            lines = handle_focus_cmd(self.home, arg)
            for line in lines:
                self._pm(src, line)
            self.handled.append(f"focus:{src}:{arg or '*'}")
            log.info("cmd=focus nick=%s replies=%s", src, len(lines))
            return True
        if is_unfocus(text) or parse_unfocus_cmd(text) is not None:
            arg = parse_unfocus_cmd(text)
            if arg is None:
                arg = ""
            if not self._focus_mutator_ok(src):
                live = self.mode_grants is not None
                acct = self._account_for_nick(src) if live else None
                self._pm(src, "unfocus: denied (owner account required)")
                self.handled.append(f"unfocus_denied:{src}")
                log.info(
                    "cmd=unfocus nick=%s denied account=%s live=%s",
                    src,
                    (acct or "none"),
                    live,
                )
                return True
            lines = handle_unfocus_cmd(self.home, arg)
            for line in lines:
                self._pm(src, line)
            self.handled.append(f"unfocus:{src}:{arg or '*'}")
            log.info("cmd=unfocus nick=%s replies=%s", src, len(lines))
            return True
        return False

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
        if self._handle_focus_cmds(src, text):
            return
        if self._handle_ignore_cmds(src, text):
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
            elif self._handle_focus_cmds(src, text):
                return
            elif self._handle_ignore_cmds(src, text):
                return
            return
        # FR #106: !bored → assign; ACK/DONE still recorded here.
        if is_bored(text):
            self._handle_bored_assign(src, target)
            return
        ack = parse_ack(text)
        if ack:
            # K2: only real worker nicks may ACK in their own shop
            if bored_gate(self.home, src, target, skip_idle_check=True) != "ok":
                self.handled.append(f"ignored_ack_bad_nick:{src}")
                return
            if ack.extra:
                log.info(
                    "event=ack_extra nick=%s repo=%s#%s extra=%s",
                    src,
                    ack.repo,
                    ack.number,
                    ack.extra[:120],
                )
            # K3 / FR #4 / FR #102: accept when matched; always record busy.
            st, row = accept_job(self.home, src, target, ack.task, ack.repo, ack.number)
            counts = queue_counts(self.home)
            job = f"{ack.repo} {ack.task} #{ack.number}"
            try:
                if st == "accepted":
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
                # Always mirror busy — including no_match (FR #102).
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
            self.assign_state.on_ack(canonical_worker_nick(src) or src)
            if st == "accepted":
                self.handled.append(f"ack:{src}:{ack.repo}#{ack.number}")
                log.info(
                    "event=ack nick=%s job=%s#%s mode=%s",
                    src,
                    ack.repo,
                    ack.number,
                    ack.task,
                )
                log.info("cmd=ack nick=%s repo=%s#%s", src, ack.repo, ack.number)
            else:
                self.handled.append(f"ack_no_match:{src}:{ack.repo}#{ack.number}")
                from .queue import _ack_no_match_logged

                key = f"{src}:{ack.repo}#{ack.number}:{ack.task}"
                if key not in _ack_no_match_logged:
                    _ack_no_match_logged.add(key)
                    log.warning(
                        "event=ack_no_match nick=%s repo=%s#%s task=%s reason=no_queue_row",
                        src,
                        ack.repo,
                        ack.number,
                        ack.task,
                    )
            return
        if looks_like_ack(text):
            # FR #102: never silent-drop a broken ACK line.
            if bored_gate(self.home, src, target, skip_idle_check=True) == "ok":
                hint = ack_format_hint()
                log.warning("event=ack_reject nick=%s reason=bad_format text=%s", src, (text or "")[:80])
                self._pm(src, hint)
                self.handled.append(f"ack_reject:{src}")
            else:
                self.handled.append(f"ignored_ack_bad_nick:{src}")
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
            self.assign_state.on_done(canonical_worker_nick(src) or src)
            self.handled.append(f"done:{src}:{done.repo}#{done.number}:{st}")
            log.info(
                "event=done nick=%s job=%s#%s mode=%s",
                src,
                done.repo,
                done.number,
                (done.task or "FR").upper(),
            )
            log.info("cmd=done nick=%s repo=%s#%s", src, done.repo, done.number)
            return

    def _handle_bored_assign(self, src: str, target: str) -> None:
        """FR #106: trusted !bored in own shop → one assign line (or nothing queued)."""
        reason = trust_bored(src, target, is_pm=not str(target or "").startswith("#"))
        if reason != "ok":
            log.info("event=bored_ignore nick=%s target=%s reason=%s", src, target, reason)
            self.handled.append(f"bored_ignore:{src}:{reason}")
            return
        # No idle wait — !bored is join/DONE signal, not 120s idle.
        if bored_gate(self.home, src, target, skip_idle_check=True) != "ok":
            log.info("event=bored_ignore nick=%s target=%s reason=gate", src, target)
            self.handled.append(f"bored_ignore:{src}:gate")
            return
        shop = worker_shop_channel(src) or target
        live = self._live_seats(shop)
        live.add(canonical_worker_nick(src) or src)
        decision = self.assign_state.decide(
            self.home,
            src,
            target,
            live_nicks=live,
            chair_nick=self.nick,
        )
        if decision.action == "assign" and decision.line:
            self._shop_privmsg(target, decision.line)
            self.handled.append(f"assign:{src}:{decision.line}")
            log.info("event=assign nick=%s line=%s", src, decision.line[:120])
            return
        if decision.action == "nothing" and decision.line:
            self._shop_privmsg(target, decision.line)
            self.handled.append(f"assign_empty:{src}")
            log.info("event=assign_empty nick=%s reason=%s", src, decision.reason)
            return
        self.handled.append(f"bored_skip:{src}:{decision.action}:{decision.reason}")
        log.info(
            "event=bored_skip nick=%s action=%s reason=%s",
            src,
            decision.action,
            decision.reason,
        )

    def _handle_quit_raw(self, line: str) -> None:
        """FR #102: on QUIT, release that nick's accepted jobs + workers entry."""
        # :nick!user@host QUIT :reason
        raw = (line or "").strip()
        if " QUIT" not in raw.upper():
            return
        if not raw.startswith(":"):
            return
        prefix = raw[1:].split(" ", 1)[0]
        nick = prefix.split("!", 1)[0].strip()
        if not nick or nick.lower() == (self.nick or "").lower():
            return
        from .nicks import is_worker_nick

        if not is_worker_nick(nick):
            return
        released = release_worker(self.home, nick, reason="quit")
        self.handled.append(f"quit_release:{nick}:{len(released)}")
        log.info("event=quit_release nick=%s released=%s", nick, len(released))
        try:
            from .nicks import parse_worker_nick

            parsed = parse_worker_nick(nick)
            payload = {"op": "delete-worker", "nick": nick}
            if parsed:
                payload["machine"] = parsed[0]
                payload["pid"] = parsed[1]
            self._post_report(payload)
        except Exception as e:
            log.warning("quit_release_report_err nick=%s err=%s", nick, type(e).__name__)

    def _sweep_stale_workers(self) -> None:
        """Periodic FR #102 stale sweep + FR #106 offer timeout."""
        now = time.time()
        if now - float(getattr(self, "_last_stale_sweep", 0.0) or 0.0) < 60.0:
            return
        self._last_stale_sweep = now
        try:
            for nick in self.assign_state.expire_timed_out(now=now):
                self.handled.append(f"offer_timeout:{nick}")
        except Exception as e:
            log.warning("offer_expire_err=%s", type(e).__name__)
        expired = expire_stale_workers(self.home)
        for nick in expired:
            self.handled.append(f"stale_release:{nick}")
            log.info("event=stale_release nick=%s", nick)
            try:
                from .nicks import parse_worker_nick

                parsed = parse_worker_nick(nick)
                payload: dict = {"op": "delete-worker", "nick": nick}
                if parsed:
                    payload["machine"] = parsed[0]
                    payload["pid"] = parsed[1]
                self._post_report(payload)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_outbox()
            except Exception as e:
                log.warning("drain_outbox_err=%s", type(e).__name__)
            try:
                self._sweep_stale_workers()
            except Exception as e:
                log.warning("stale_sweep_err=%s", type(e).__name__)
            try:
                # K13 / FR #14: ensure socket before poll (recover after Ergo blip)
                ensure = getattr(self.client, "ensure_connected", None)
                if callable(ensure) and getattr(self.client, "sock", None) is None:
                    if ensure(deadline=time.time() + 5.0):
                        if self.auto_join_ctrl is not None:
                            self.auto_join_ctrl.note_reconnect()
                        else:
                            self.client.join("#bobiverse", *self.shops)
                msg = self.client.wait_privmsg(timeout=0.3)
            except OSError:
                # FR #46 / #14: native TLS client reconnect-in-place (backoff on throttle)
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
            # FR #106: ear !bored OFFER path retired — Jeeves assigns.
            self.offers.append(f"retired_bored:{src}")
            log.info("event=ear_bored_retired nick=%s shop=%s", src, self.shop)
