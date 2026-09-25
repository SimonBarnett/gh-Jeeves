"""Minimal loopback IRC daemon for G1 (not live Ergo)."""

from __future__ import annotations

import select
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

# issue #74: bound raw line retention
RAW_INBOX_MAX = 500


@dataclass
class Client:
    sock: socket.socket
    addr: tuple
    nick: str = ""
    user: str = ""
    registered: bool = False
    channels: set[str] = field(default_factory=set)
    buf: str = ""
    account: str = ""  # FR #52 services account (test/SASL stand-in)
    host: str = "local"

    def __hash__(self) -> int:
        return hash(id(self.sock))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Client) and self.sock is other.sock


class LocalIrcd:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._clients: list[Client] = []
        self._chan_members: dict[str, set[Client]] = defaultdict(set)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.privmsg_log: list[tuple[str, str, str]] = []  # nick, target, text
        self.on_privmsg: Callable[[str, str, str], None] | None = None
        # FR #52: channel -> nick -> mode chars
        self._chan_modes: dict[str, dict[str, str]] = defaultdict(dict)
        self.mode_log: list[str] = []
        # FR #55: channels known to the server (for LIST), even with zero members
        self._known_channels: set[str] = set()

    def ensure_channel(self, channel: str) -> None:
        """Create/advertise a channel name for LIST (tests create #b later)."""
        ch = channel if channel.startswith("#") else f"#{channel}"
        with self._lock:
            self._known_channels.add(ch.lower())
            self._chan_members.setdefault(ch.lower(), set())

    @property
    def bound_port(self) -> int:
        return int(self.port)

    def start(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(32)
        s.setblocking(False)
        self._sock = s
        self.port = s.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="local-ircd", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            for c in list(self._clients):
                try:
                    c.sock.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _send(self, c: Client, line: str) -> None:
        data = (line.rstrip("\r\n") + "\r\n").encode("utf-8", errors="replace")
        try:
            c.sock.sendall(data)
        except OSError:
            pass

    def _broadcast_channel(self, channel: str, line: str, exclude: Client | None = None) -> None:
        with self._lock:
            members = list(self._chan_members.get(channel.lower(), set()))
        for m in members:
            if m is exclude:
                continue
            self._send(m, line)

    def _handle_line(self, c: Client, line: str) -> None:
        line = line.strip("\r\n")
        if not line:
            return
        if line.startswith(":"):
            parts = line.split(" ", 2)
            line = parts[1] + ((" " + parts[2]) if len(parts) > 2 else "") if len(parts) > 1 else line
        bits = line.split(" ")
        cmd = bits[0].upper()
        if cmd == "NICK" and len(bits) >= 2:
            c.nick = bits[1].lstrip(":")
            if c.user:
                c.registered = True
                self._send(c, f":local 001 {c.nick} :Welcome")
            return
        if cmd == "USER" and len(bits) >= 2:
            c.user = bits[1]
            if c.nick:
                c.registered = True
                self._send(c, f":local 001 {c.nick} :Welcome")
            return
        if cmd == "PING":
            token = bits[1] if len(bits) > 1 else "local"
            self._send(c, f"PONG :{token.lstrip(':')}")
            return
        if cmd == "ACCOUNT" and len(bits) >= 2 and c.registered:
            # test helper: ACCOUNT <name>  — sets services account for this client
            c.account = bits[1].lstrip(":")
            notice = f":{c.nick}!u@{c.host} ACCOUNT {c.account}"
            with self._lock:
                peers = list(self._clients)
            for ppeer in peers:
                if ppeer is not c:
                    self._send(ppeer, notice)
            return
        if cmd == "HOST" and len(bits) >= 2 and c.registered:
            c.host = bits[1].lstrip(":")
            return
        if cmd == "JOIN" and len(bits) >= 2 and c.registered:
            for raw in bits[1].split(","):
                ch = raw if raw.startswith("#") else f"#{raw}"
                ch_l = ch.lower()
                c.channels.add(ch_l)
                with self._lock:
                    self._known_channels.add(ch_l)
                    self._chan_members[ch_l].add(c)
                # extended-join: nick!u@host JOIN #ch account :realname
                acct = c.account or "*"
                join_line = f":{c.nick}!u@{c.host} JOIN {ch} {acct} :{c.nick}"
                self._send(c, join_line)
                self._broadcast_channel(ch_l, join_line, exclude=c)
                nicks = " ".join(m.nick for m in self._chan_members[ch_l] if m.nick)
                self._send(c, f":local 353 {c.nick} = {ch} :{nicks}")
                self._send(c, f":local 366 {c.nick} {ch} :End")
            return
        if cmd == "MODE" and len(bits) >= 3 and c.registered:
            ch = bits[1]
            ch_l = ch.lower() if ch.startswith("#") else f"#{ch}".lower()
            spec = bits[2]
            args = [a.lstrip(":") for a in bits[3:]]
            adding = True
            ai = 0
            with self._lock:
                bucket = self._chan_modes.setdefault(ch_l, {})
                for chm in spec:
                    if chm == "+":
                        adding = True
                        continue
                    if chm == "-":
                        adding = False
                        continue
                    if chm in "ohv" and ai < len(args):
                        nk = args[ai].lower()
                        ai += 1
                        cur = set(bucket.get(nk, ""))
                        if adding:
                            cur.add(chm)
                        else:
                            cur.discard(chm)
                        bucket[nk] = "".join(sorted(cur))
            mode_line = f":{c.nick}!u@{c.host} MODE {ch} {spec} " + " ".join(args)
            mode_line = mode_line.rstrip()
            self.mode_log.append(mode_line)
            self._broadcast_channel(ch_l, mode_line, exclude=None)
            return
        if cmd == "LIST" and c.registered:
            with self._lock:
                names = sorted(set(self._known_channels) | set(self._chan_members.keys()))
                counts = {k: len(self._chan_members.get(k, set())) for k in names}
            for ch_l in names:
                ch = ch_l if ch_l.startswith("#") else f"#{ch_l}"
                n = counts.get(ch_l, 0)
                self._send(c, f":local 322 {c.nick} {ch} {n} :channel")
            self._send(c, f":local 323 {c.nick} :End of LIST")
            return
        if cmd == "KICK" and len(bits) >= 3 and c.registered:
            ch = bits[1] if bits[1].startswith("#") else f"#{bits[1]}"
            victim = bits[2]
            reason = line.split(":", 1)[1] if ":" in line else "kicked"
            with self._lock:
                members = list(self._chan_members.get(ch.lower(), set()))
            kick_line = f":{c.nick}!u@local KICK {ch} {victim} :{reason}"
            for m in members:
                self._send(m, kick_line)
            # remove victim from channel
            with self._lock:
                for m in list(self._chan_members.get(ch.lower(), set())):
                    if m.nick.lower() == victim.lower():
                        self._chan_members[ch.lower()].discard(m)
                        m.channels.discard(ch.lower())
            return
        if cmd == "PRIVMSG" and len(bits) >= 3 and c.registered:
            target = bits[1]
            # text after first :
            if ":" in line:
                text = line.split(":", 1)[1]
            else:
                text = " ".join(bits[2:])
            self.privmsg_log.append((c.nick, target, text))
            if self.on_privmsg:
                try:
                    self.on_privmsg(c.nick, target, text)
                except Exception:
                    pass
            prefix = f":{c.nick}!u@local PRIVMSG {target} :{text}"
            if target.startswith("#"):
                self._broadcast_channel(target.lower(), prefix, exclude=c)
            else:
                # PM to nick
                with self._lock:
                    dests = [x for x in self._clients if x.nick.lower() == target.lower()]
                for d in dests:
                    self._send(d, prefix)
            return
        if cmd == "QUIT":
            self._drop(c)
            return

    def _drop(self, c: Client) -> None:
        with self._lock:
            if c in self._clients:
                self._clients.remove(c)
            for ch in list(c.channels):
                self._chan_members[ch].discard(c)
        try:
            c.sock.close()
        except OSError:
            pass

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            with self._lock:
                clients = list(self._clients)
            rlist = [self._sock] + [c.sock for c in clients]
            try:
                readable, _, _ = select.select(rlist, [], [], 0.2)
            except (OSError, ValueError):
                time.sleep(0.05)
                continue
            if self._sock in readable:
                try:
                    cs, addr = self._sock.accept()
                    cs.setblocking(False)
                    client = Client(sock=cs, addr=addr)
                    with self._lock:
                        self._clients.append(client)
                except OSError:
                    pass
            for c in clients:
                if c.sock not in readable:
                    continue
                try:
                    data = c.sock.recv(4096)
                except OSError:
                    self._drop(c)
                    continue
                if not data:
                    self._drop(c)
                    continue
                c.buf += data.decode("utf-8", errors="replace")
                while "\n" in c.buf:
                    raw, c.buf = c.buf.split("\n", 1)
                    self._handle_line(c, raw)


class IrcClient:
    """Tiny blocking IRC client for tests / fake worker / ear / Jeeves."""

    def __init__(self, host: str, port: int, nick: str):
        self.host = host
        self.port = port
        self.nick = nick
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(0.5)
        self.buf = ""
        self.inbox: list[tuple[str, str, str]] = []  # src, target, text
        self.raw_inbox: deque[str] = deque(maxlen=RAW_INBOX_MAX)  # FR #55 + #74 bound
        self.on_raw: Callable[[str], None] | None = None
        self._send(f"NICK {nick}")
        self._send(f"USER {nick} 0 * :{nick}")
        self._drain_until(lambda: True, timeout=2.0)

    def _send(self, line: str) -> None:
        self.sock.sendall((line.rstrip("\r\n") + "\r\n").encode("utf-8"))

    def send_raw(self, line: str) -> None:
        """FR #52/#55: MODE / LIST / raw protocol."""
        self._send(line)

    def set_account(self, account: str) -> None:
        """FR #52 test: bind services account on local ircd."""
        self._send(f"ACCOUNT {account}")

    def set_host(self, host: str) -> None:
        self._send(f"HOST {host}")

    def join(self, *channels: str) -> None:
        for ch in channels:
            self._send(f"JOIN {ch}")
        time.sleep(0.05)

    def privmsg(self, target: str, text: str) -> None:
        self._send(f"PRIVMSG {target} :{text}")

    def kick(self, channel: str, nick: str, reason: str = "out") -> None:
        self._send(f"KICK {channel} {nick} :{reason}")

    def _parse(self, line: str) -> None:
        self.raw_inbox.append(line)
        if self.on_raw:
            try:
                self.on_raw(line)
            except Exception:
                pass
        if line.upper().startswith("PING"):
            token = line.split(":", 1)[1] if ":" in line else "x"
            self._send(f"PONG :{token}")
            return
        # :nick!u@h PRIVMSG #ch :text
        if " PRIVMSG " not in line:
            return
        try:
            prefix, rest = line[1:].split(" ", 1) if line.startswith(":") else ("", line)
            src = prefix.split("!", 1)[0] if prefix else ""
            parts = rest.split(" ", 2)
            if len(parts) < 3:
                return
            target = parts[1]
            text = parts[2][1:] if parts[2].startswith(":") else parts[2]
            self.inbox.append((src, target, text))
        except ValueError:
            return

    def _drain_until(self, pred, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            self.buf += data.decode("utf-8", errors="replace")
            while "\n" in self.buf:
                raw, self.buf = self.buf.split("\n", 1)
                self._parse(raw.strip("\r"))
        return pred()

    def wait_privmsg(self, predicate=None, timeout: float = 5.0) -> tuple[str, str, str] | None:
        def pred():
            if predicate is None:
                return bool(self.inbox)
            return any(predicate(m) for m in self.inbox)

        self._drain_until(pred, timeout=timeout)
        if predicate is None:
            return self.inbox.pop(0) if self.inbox else None
        for i, m in enumerate(self.inbox):
            if predicate(m):
                return self.inbox.pop(i)
        return None

    def close(self) -> None:
        try:
            self._send("QUIT :bye")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
