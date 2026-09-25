"""python -m jeeves — production BobJeeves entry (FR #39).

Modes:
  chair     — IRC chair (local ircd for G1; TLS Ergo when --tls)
  receiver  — GIT/report/intake HTTP on loopback
  all       — both (default for Windows service)

Never touches Ergo/BobIrcd config. Queue source of truth: BOB_DIGEST_HOME/queue.json.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from .env_secrets import hydrate_secrets_from_files


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m jeeves", description="BobJeeves chair + GIT receiver")
    p.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=("chair", "receiver", "all", "dry-run", "health"),
        help="Process role (default all); health = report-only IRC/service probe (FR #8)",
    )
    p.add_argument("--nick", default=os.environ.get("AGENTIC_IRC_CHAIR_NICK") or "Jeeves")
    p.add_argument("--host", default=os.environ.get("AGENTIC_IRC_HOST") or "127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("AGENTIC_IRC_PORT") or "0"))
    p.add_argument("--tls", action="store_true", help="TLS to Ergo (production)")
    p.add_argument(
        "--tls-insecure",
        action="store_true",
        help="TLS without cert verify (G1 local self-signed only)",
    )
    p.add_argument(
        "--tls-cafile",
        default=os.environ.get("JEEVES_TLS_CAFILE") or "",
        help="Optional PEM CA bundle for Ergo",
    )
    p.add_argument(
        "--tls-pin-sha256",
        default=os.environ.get("JEEVES_TLS_PIN_SHA256") or "",
        help="Optional SHA-256 pin of server DER cert (hex)",
    )
    p.add_argument(
        "--sasl-user",
        default=os.environ.get("AGENTIC_IRC_SASL_USER") or "",
    )
    p.add_argument(
        "--sasl-password",
        default=os.environ.get("AGENTIC_IRC_SASL_PASSWORD") or "",
    )
    p.add_argument(
        "--digest-home",
        default=os.environ.get("BOB_DIGEST_HOME") or "",
        help="Queue + chair-outbox home (must differ from --jeeves-home)",
    )
    p.add_argument(
        "--jeeves-home",
        default=os.environ.get("JEEVES_HOME") or os.environ.get("AGENTIC_IRC_HOME") or "",
        help="Chair process home (logs, singleton)",
    )
    p.add_argument(
        "--report-url",
        default=os.environ.get("BOB_REPORT_URL") or "http://127.0.0.1:0",
        help="Base URL for digest report POSTs (no path)",
    )
    p.add_argument(
        "--receiver-bind",
        default=os.environ.get("BOB_REPORT_BIND") or "127.0.0.1",
    )
    p.add_argument(
        "--receiver-port",
        type=int,
        # FR #48: ionos IIS proxies to 19781; helpers/skill default combined topology
        default=int(os.environ.get("BOB_REPORT_PORT") or "19781"),
    )
    p.add_argument(
        "--shops",
        default=os.environ.get("JEEVES_SHOPS")
        or "#flamingo,#ionos,#marchhare,#ce-priority-dev1",
        help="Comma shop channels for silent ACK/DONE listen",
    )
    p.add_argument("--password", default=os.environ.get("AGENTIC_IRC_PASSWORD") or "")
    p.add_argument("--no-singleton", action="store_true")
    p.add_argument(
        "--resync-interval",
        type=float,
        default=float(os.environ.get("JEEVES_RESYNC_INTERVAL") or 15 * 60),
        help="GitHub resync period seconds (default 900). FR #49 wires this.",
    )
    p.add_argument(
        "--no-resync",
        action="store_true",
        help="Disable GitHub resync (same as JEEVES_RESYNC_DISABLE=1)",
    )
    p.add_argument(
        "--replay-outbox",
        action="store_true",
        help="FR #71: drain chair-outbox from offset 0 when no pos file (default: start at EOF, no flood)",
    )
    return p.parse_args(argv)


def dry_run_plan(args: argparse.Namespace) -> dict:
    from .prod_receiver import digest_home_from_env, jeeves_home_from_env

    jh = Path(args.jeeves_home).expanduser() if args.jeeves_home else jeeves_home_from_env()
    dh = Path(args.digest_home).expanduser() if args.digest_home else digest_home_from_env()
    from .versioning import check_drift, running_version, version_report_payload

    ver = version_report_payload()
    return {
        "ok": True,
        "mode": args.mode,
        "nick": args.nick,
        "jeeves_home": str(jh),
        "digest_home": str(dh),
        "homes_distinct": str(jh).rstrip("\\/").lower() != str(dh).rstrip("\\/").lower(),
        "receiver": f"{args.receiver_bind}:{args.receiver_port}",
        "shops": [s.strip() for s in args.shops.split(",") if s.strip()],
        "resync_interval_s": args.resync_interval,
        "resync_on_start": not bool(getattr(args, "no_resync", False)),
        "never_touch": ["Ergo", "BobIrcd", "ircd.yaml"],
        "queue_path": str(dh / "queue.json"),
        "entry": "python -m jeeves",
        "version": ver.get("version"),
        "jeeves_version": ver.get("jeeves_version"),
        "version_drift": ver.get("version_drift"),
        "running": running_version().report_string,
        "drift_ok": check_drift().ok,
    }


def main(argv: list[str] | None = None) -> int:
    hydrate_secrets_from_files()
    args = _parse(argv)
    if args.mode == "dry-run":
        import json

        print(json.dumps(dry_run_plan(args), indent=2))
        return 0
    if args.mode == "health":
        from . import health as health_mod

        h_argv: list[str] = []
        if args.host:
            h_argv += ["--host", args.host]
        if args.port:
            h_argv += ["--port", str(args.port)]
        if args.tls:
            h_argv.append("--tls")
        else:
            h_argv.append("--no-tls")
        h_argv.append("--json")
        return health_mod.main(h_argv)

    from .prod_receiver import (
        ProdReceiver,
        assert_homes_distinct,
        digest_home_from_env,
        jeeves_home_from_env,
    )
    from .singleton import SingletonError, acquire

    jh = Path(args.jeeves_home).expanduser() if args.jeeves_home else jeeves_home_from_env()
    dh = Path(args.digest_home).expanduser() if args.digest_home else digest_home_from_env()
    assert_homes_distinct(jh, dh)
    jh.mkdir(parents=True, exist_ok=True)
    dh.mkdir(parents=True, exist_ok=True)
    os.environ["BOB_DIGEST_HOME"] = str(dh)
    os.environ["JEEVES_HOME"] = str(jh)
    os.environ["AGENTIC_IRC_HOME"] = str(jh)

    # FR #73: rotating bobjeeves-service.log (connect/auth/joins/LIST/cmds)
    from .service_log import configure_service_logging

    log_path = configure_service_logging(jh)
    print(f"INFO service_log {log_path}", flush=True)

    # K5: stamp running version for webhook drift (FR #6)
    try:
        from .versioning import write_version_stamp

        write_version_stamp(dh)
        print(f"INFO version stamp {dh / 'jeeves_version.json'}", flush=True)
    except Exception as exc:
        print(f"INFO version stamp skip {type(exc).__name__}", flush=True)

    if not args.no_singleton:
        try:
            acquire(jh, role=args.mode)
        except SingletonError as e:
            print(f"ERROR singleton {e}", flush=True)
            return 2

    receiver: ProdReceiver | None = None
    report_base = args.report_url.rstrip("/")

    if args.mode in ("receiver", "all"):
        # FR #47: arm X-Bob-Secret from digest-home bob.secret / env (never log value)
        from .auth_secret import load_bob_secret

        _sec = load_bob_secret(homes=[dh])
        receiver = ProdReceiver(
            dh,
            host=args.receiver_bind,
            port=args.receiver_port,
            bob_secret=_sec if _sec else None,
            require_secret=True if _sec else None,
        )
        port = receiver.start(background=True)
        report_base = f"http://{args.receiver_bind}:{port}"
        print(f"INFO receiver listen {args.receiver_bind}:{port} digest_home={dh}", flush=True)
        # point chair report at ourselves when all
        if args.mode == "all" or args.report_url.endswith(":0"):
            os.environ["BOB_REPORT_URL"] = report_base

    if args.mode in ("chair", "all"):
        shops = [s.strip() for s in args.shops.split(",") if s.strip()]
        # G1 / lab: plain TCP to local ircd; production: --tls to Ergo
        from .local_ircd import IrcClient
        from .roles import JeevesChair
        from .resync import ResyncConfig, ResyncScheduler

        # Use digest home for queue + chair-outbox (production contract)
        chair_queue_home = dh
        host = args.host
        port = int(args.port)
        if port <= 0 and not args.tls:
            print(
                "ERROR chair needs --port (local ircd) or production Ergo :6697 with --tls",
                flush=True,
            )
            if receiver:
                receiver.stop()
            return 2

        # FR #49: wire ResyncScheduler on start so !list is populated
        from .resync import ResyncConfigError, build_resync_scheduler, resync_disabled

        if args.no_resync:
            os.environ["JEEVES_RESYNC_DISABLE"] = "1"

        sched = None
        if not resync_disabled():
            try:
                sched = build_resync_scheduler(
                    chair_queue_home,
                    interval_s=float(args.resync_interval),
                    require_token=True,
                    jeeves_home=jh,
                )
            except ResyncConfigError as exc:
                print(f"ERROR resync config: {exc}", flush=True)
                if receiver:
                    receiver.stop()
                return 2

        # For unit/service smoke without IRC, allow JEEVES_CHAIR_NO_IRC=1
        if os.environ.get("JEEVES_CHAIR_NO_IRC") == "1":
            print("INFO chair no-irc smoke mode digest_home=" + str(dh), flush=True)
            if sched is not None:
                # still run start resync so queue is warm before IRC-less hold
                sched.start(run_immediately=True)
                print(
                    f"INFO resync-on-start runs={sched.runs} interval_s={args.resync_interval}",
                    flush=True,
                )
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                if sched is not None:
                    sched.stop()
                pass
        else:
            # Prefer native TLS when --tls (if tls_irc present); else plain client
            client = None
            if args.tls:
                try:
                    from .tls_irc import TlsIrcClient

                    if port <= 0:
                        port = int(os.environ.get("AGENTIC_IRC_PORT") or "6697")
                    if not os.environ.get("AGENTIC_IRC_HOST") and host in (
                        "127.0.0.1",
                        "0.0.0.0",
                        "",
                    ):
                        host = "irc.ntsa.uk"
                    client = TlsIrcClient(
                    host,
                    port,
                    args.nick,
                    tls=True,
                    insecure=bool(args.tls_insecure),
                    cafile=args.tls_cafile or None,
                    cert_pin_sha256=args.tls_pin_sha256 or None,
                    password=args.password or "",
                    sasl_user=args.sasl_user or "",
                    sasl_password=args.sasl_password or "",
                )
                    print(f"INFO native TLS IRC client host={host}:{port}", flush=True)
                except ImportError:
                    print(
                        "ERROR --tls requires jeeves.tls_irc (FR #46); plain mode without --tls",
                        flush=True,
                    )
                    if receiver:
                        receiver.stop()
                    if sched is not None:
                        sched.stop()
                    return 2
            chair_kwargs = dict(
                host=host,
                port=port,
                home=chair_queue_home,
                report_url=report_base,
                nick=args.nick,
                shops=shops,
                resync_scheduler=sched,
                replay_outbox=bool(getattr(args, "replay_outbox", False)),
            )
            # optional client= only if JeevesChair supports it
            import inspect

            from .roles import JeevesChair as _JC

            if client is not None and "client" in inspect.signature(_JC.__init__).parameters:
                chair_kwargs["client"] = client
            chair = _JC(**chair_kwargs)
            chair.start()
            print(
                f"INFO chair nick={args.nick} host={host}:{port} shops={shops} digest={dh} "
                f"resync={'on' if sched else 'off'} interval_s={args.resync_interval}",
                flush=True,
            )
            try:
                while not chair._stop.is_set():  # noqa: SLF001
                    time.sleep(0.5)
            except KeyboardInterrupt:
                chair.stop()

    if args.mode == "receiver" and receiver:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    if receiver:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
