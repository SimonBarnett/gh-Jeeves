"""Native TLS IRC client for BobJeeves (FR #46).

No agentic_irc import. Speaks the same surface as ``local_ircd.IrcClient``
(join / privmsg / wait_privmsg / close) so ``JeevesChair`` stays unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Callable

from .backoff import is_throttle_error, throttle_delay_s
from .local_ircd import LocalIrcd

FLOOD_S = 0.8


def build_ssl_context(
    *,
    insecure: bool = False,
    cafile: str | None = None,
    cert_pin_sha256: str | None = None,
) -> ssl.SSLContext:
    """
    TLS context for Ergo.

    - default: system CAs, CERT_REQUIRED
    - insecure: CERT_NONE (G1 local self-signed only)
    - cafile: optional PEM CA bundle
    - cert_pin_sha256: optional lowercase hex SHA-256 of DER cert (checked after connect)
    """
    if insecure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context(cafile=cafile or None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    # pin is enforced in connect_tls after handshake
    ctx._jeeves_pin = (cert_pin_sha256 or "").strip().lower()  # type: ignore[attr-defined]
    return ctx


def _peer_cert_sha256(ssock: ssl.SSLSocket) -> str:
    der = ssock.getpeercert(binary_form=True)
    if not der:
        return ""
    return hashlib.sha256(der).hexdigest()


def connect_tls(
    host: str,
    port: int,
    *,
    insecure: bool = False,
    cafile: str | None = None,
    cert_pin_sha256: str | None = None,
    timeout: float = 15.0,
) -> ssl.SSLSocket:
    ctx = build_ssl_context(insecure=insecure, cafile=cafile, cert_pin_sha256=cert_pin_sha256)
    raw = socket.create_connection((host, port), timeout=timeout)
    ssock = ctx.wrap_socket(raw, server_hostname=None if insecure else host)
    pin = (cert_pin_sha256 or getattr(ctx, "_jeeves_pin", "") or "").strip().lower()
    if pin:
        got = _peer_cert_sha256(ssock)
        if got != pin:
            ssock.close()
            raise ssl.SSLError(f"cert pin mismatch got={got[:16]}… want={pin[:16]}…")
    return ssock  # type: ignore[return-value]


class TlsIrcClient:
    """Blocking IRC client over TLS (or plain if tls=False) with optional SASL."""

    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        *,
        tls: bool = True,
        insecure: bool = False,
        cafile: str | None = None,
        cert_pin_sha256: str | None = None,
        password: str = "",
        sasl_user: str = "",
        sasl_password: str = "",
        flood_s: float = FLOOD_S,
        connect_timeout: float = 15.0,
    ):
        self.host = host
        self.port = int(port)
        self.nick = nick
        self.tls = tls
        self.insecure = insecure
        self.cafile = cafile
        self.cert_pin_sha256 = cert_pin_sha256
        self.password = password or os.environ.get("AGENTIC_IRC_PASSWORD") or ""
        self.sasl_user = sasl_user or os.environ.get("AGENTIC_IRC_SASL_USER") or ""
        self.sasl_password = sasl_password or os.environ.get("AGENTIC_IRC_SASL_PASSWORD") or ""
        self.flood_s = flood_s
        self.connect_timeout = connect_timeout
        self.sock: socket.socket | None = None
        self.buf = ""
        self.inbox: list[tuple[str, str, str]] = []
        self.raw_inbox: list[str] = []
        self.on_raw: Callable[[str], None] | None = None
        self._lock = threading.Lock()
        self.reconnect_count = 0
        self.last_throttle = False
        self._connect_and_register()

    def _connect_and_register(self) -> None:
        if self.tls:
            self.sock = connect_tls(
                self.host,
                self.port,
                insecure=self.insecure,
                cafile=self.cafile,
                cert_pin_sha256=self.cert_pin_sha256,
                timeout=self.connect_timeout,
            )
        else:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            )
        self.sock.settimeout(0.5)
        self.buf = ""
        # CAP/SASL + FR #52 identity (account-notify / extended-join)
        if self.sasl_user and self.sasl_password:
            self._send("CAP LS 302")
            self._send("NICK " + self.nick)
            self._send(f"USER {self.nick} 0 * :{self.nick}")
            self._drain_until(lambda: True, timeout=2.0)
            self._send("CAP REQ :sasl account-notify extended-join")
            self._send("AUTHENTICATE PLAIN")
            # wait AUTHENTICATE +
            self._drain_until(lambda: True, timeout=2.0)
            token = base64.b64encode(
                f"\0{self.sasl_user}\0{self.sasl_password}".encode("utf-8")
            ).decode("ascii")
            self._send("AUTHENTICATE " + token)
            self._send("CAP END")
        else:
            if self.password:
                self._send("PASS " + self.password)
            self._send("NICK " + self.nick)
            self._send(f"USER {self.nick} 0 * :{self.nick}")
            # still request identity CAPs when present (Ergo may grant without SASL)
            self._send("CAP LS 302")
            self._drain_until(lambda: True, timeout=1.0)
            self._send("CAP REQ :account-notify extended-join")
            self._send("CAP END")
        self._drain_until(lambda: True, timeout=3.0)

    def reconnect(self, *, attempt: int | None = None) -> float:
        """Close and reconnect. Returns backoff seconds applied."""
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        n = attempt if attempt is not None else (self.reconnect_count + 1)
        delay = 0.0
        if self.last_throttle or n > 1:
            delay = throttle_delay_s(n)
            time.sleep(delay)
        self.reconnect_count = n
        self._connect_and_register()
        return delay

    def _send(self, line: str) -> None:
        assert self.sock is not None
        data = (line.rstrip("\r\n") + "\r\n").encode("utf-8")
        with self._lock:
            self.sock.sendall(data)

    def send_raw(self, line: str) -> None:
        """FR #52: MODE and other non-PRIVMSG lines for ModeGrantController."""
        self._send(line)

    def join(self, *channels: str) -> None:
        for ch in channels:
            self._send(f"JOIN {ch}")
            time.sleep(min(0.05, self.flood_s))

    def privmsg(self, target: str, text: str) -> None:
        self._send(f"PRIVMSG {target} :{text}")
        time.sleep(self.flood_s)

    def _parse(self, line: str) -> None:
        if not line:
            return
        self.raw_inbox.append(line)
        if self.on_raw:
            try:
                self.on_raw(line)
            except Exception:
                pass
        if is_throttle_error(line):
            self.last_throttle = True
        up = line.upper()
        if up.startswith("PING") or " PING " in f" {up} ":
            # :server PING :token  OR  PING :token
            if ":" in line:
                token = line.split(":", 1)[1]
            else:
                parts = line.split()
                token = parts[-1] if parts else "x"
            self._send(f"PONG :{token}")
            return
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

    def _drain_until(self, pred: Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            if self.sock is None:
                break
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                # FR #46 AC1: drop → reconnect in place with throttle backoff
                try:
                    self.reconnect()
                except OSError:
                    break
                continue
            if not data:
                # peer closed
                try:
                    self.reconnect()
                except OSError:
                    break
                continue
            self.buf += data.decode("utf-8", errors="replace")
            while "\n" in self.buf:
                raw, self.buf = self.buf.split("\n", 1)
                self._parse(raw.strip("\r"))
                # throttle ERROR may have set last_throttle; next reconnect backs off
        return pred()

    def wait_privmsg(self, predicate=None, timeout: float = 5.0) -> tuple[str, str, str] | None:
        def pred() -> bool:
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
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None


class LocalTlsIrcd:
    """LocalIrcd behind a TLS terminator (self-signed) for G1 TLS path."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, certfile: str, keyfile: str):
        self.inner = LocalIrcd(host=host, port=0)
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lsock: socket.socket | None = None
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)

    @property
    def bound_port(self) -> int:
        return int(self.port)

    @property
    def privmsg_log(self):
        return self.inner.privmsg_log

    def start(self) -> int:
        # start plain ircd on ephemeral
        plain_port = self.inner.start()
        # TLS front listens on self.port (0 = ephemeral)
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(32)
        self._lsock = ls
        self.port = ls.getsockname()[1]
        self._plain_host = "127.0.0.1"
        self._plain_port = plain_port
        self._stop.clear()
        self._thread = threading.Thread(target=self._bridge_loop, name="tls-ircd-front", daemon=True)
        self._thread.start()
        return self.port

    def _bridge_loop(self) -> None:
        assert self._lsock is not None
        self._lsock.settimeout(0.5)
        bridges: list[threading.Thread] = []
        while not self._stop.is_set():
            try:
                cs, _addr = self._lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                ss = self._ctx.wrap_socket(cs, server_side=True)
            except ssl.SSLError:
                try:
                    cs.close()
                except OSError:
                    pass
                continue
            t = threading.Thread(target=self._pipe, args=(ss,), daemon=True)
            t.start()
            bridges.append(t)

    def _pipe(self, ssock: ssl.SSLSocket) -> None:
        """Bidirectional byte pipe TLS client <-> plain local ircd."""
        plain = socket.create_connection((self._plain_host, self._plain_port), timeout=5.0)
        plain.settimeout(0.2)
        ssock.settimeout(0.2)
        try:
            while not self._stop.is_set():
                # tls -> plain
                try:
                    data = ssock.recv(4096)
                    if not data:
                        break
                    plain.sendall(data)
                except socket.timeout:
                    pass
                except OSError:
                    break
                # plain -> tls
                try:
                    data = plain.recv(4096)
                    if not data:
                        break
                    ssock.sendall(data)
                except socket.timeout:
                    pass
                except OSError:
                    break
        finally:
            try:
                ssock.close()
            except OSError:
                pass
            try:
                plain.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._lsock:
            try:
                self._lsock.close()
            except OSError:
                pass
        self.inner.stop()
        if self._thread:
            self._thread.join(timeout=2.0)


def make_self_signed_cert(dir_path: Path) -> tuple[Path, Path]:
    """Create a short-lived self-signed cert for G1 (stdlib only if possible)."""
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    cert = dir_path / "g1.pem"
    key = dir_path / "g1.key"
    if cert.is_file() and key.is_file():
        return cert, key
    # Prefer cryptography if present; else openssl CLI; else raise
    try:
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert_obj = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key_obj.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key_obj, hashes.SHA256())
        )
        key.write_bytes(
            key_obj.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))
        return cert, key
    except ImportError:
        pass
    import subprocess

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def assert_no_agentic_irc_import(root: Path | None = None) -> list[str]:
    """Static guard: gh-Jeeves src must not import or spawn agentic_irc."""
    import re

    root = Path(root or Path(__file__).resolve().parent)
    # Real import/spawn forms only (not docstrings that mention the ban).
    pat = re.compile(
        r"^\s*(?:import\s+agentic_irc\b|from\s+agentic_irc\b|"
        r".*(?:Popen|subprocess|run)\s*\(.*agentic_irc|"
        r".*(?:Popen|subprocess|run)\s*\(.*irc_agent\.py)",
    )
    hits: list[str] = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if pat.search(line):
                hits.append(f"{p.name}:{i}:{s[:120]}")
    return hits
