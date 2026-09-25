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


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m jeeves", description="BobJeeves chair + GIT receiver")
    p.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=("chair", "receiver", "all", "dry-run"),
        help="Process role (default all)",
    )
    p.add_argument("--nick", default=os.environ.get("AGENTIC_IRC_CHAIR_NICK") or "Jeeves")
    p.add_argument("--host", default=os.environ.get("AGENTIC_IRC_HOST") or "127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("AGENTIC_IRC_PORT") or "0"))
    p.add_argument("--tls", action="store_true", help="TLS to Ergo (production)")
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
        default=int(os.environ.get("BOB_REPORT_PORT") or "8765"),
    )
    p.add_argument(
        "--shops",
        default=os.environ.get("JEEVES_SHOPS")
        or "#flamingo,#ionos,#marchhare,#ce-priority-dev1",
        help="Comma shop channels for silent ACK/DONE listen",
    )
    p.add_argument("--password", default=os.environ.get("AGENTIC_IRC_PASSWORD") or "")
    p.add_argument("--no-singleton", action="store_true")
    p.add_argument("--resync-interval", type=float, default=15 * 60)
    return p.parse_args(argv)


def dry_run_plan(args: argparse.Namespace) -> dict:
    from .prod_receiver import digest_home_from_env, jeeves_home_from_env

    jh = Path(args.jeeves_home).expanduser() if args.jeeves_home else jeeves_home_from_env()
    dh = Path(args.digest_home).expanduser() if args.digest_home else digest_home_from_env()
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
        "never_touch": ["Ergo", "BobIrcd", "ircd.yaml"],
        "queue_path": str(dh / "queue.json"),
        "entry": "python -m jeeves",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.mode == "dry-run":
        import json

        print(json.dumps(dry_run_plan(args), indent=2))
        return 0

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

    if not args.no_singleton:
        try:
            acquire(jh, role=args.mode)
        except SingletonError as e:
            print(f"ERROR singleton {e}", flush=True)
            return 2

    receiver: ProdReceiver | None = None
    report_base = args.report_url.rstrip("/")

    if args.mode in ("receiver", "all"):
        receiver = ProdReceiver(dh, host=args.receiver_bind, port=args.receiver_port)
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

        # Resync scheduler (15 min default) — webhook events still apply concurrently via receiver
        sched = None
        try:
            from .resync import FakeGitHub, reconcile_queue  # noqa: F401

            # Without GitHub token, skip live resync client (still startable)
            if (os.environ.get("GITHUB_TOKEN") or "").strip():
                # production resync would use LiveGitHub — optional
                pass
        except Exception:
            pass

        # For unit/service smoke without IRC, allow JEEVES_CHAIR_NO_IRC=1
        if os.environ.get("JEEVES_CHAIR_NO_IRC") == "1":
            print("INFO chair no-irc smoke mode digest_home=" + str(dh), flush=True)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        else:
            # Production TLS client is intentionally thin: prefer operator to set
            # AGENTIC_IRC via Start-BobJeeves which launches this module against
            # loopback test ircd OR injects a real client later. For Ergo TLS we
            # require agentic_irc irc_agent bridge until full wire port lands.
            if args.tls:
                print(
                    "INFO FR #39: TLS Ergo chair uses report/receiver from this package; "
                    "wire IRC via agentic_irc irc_agent --chair until full TLS client lands. "
                    f"digest_home={dh} jeeves_home={jh}",
                    flush=True,
                )
                # Keep process alive as receiver owner when mode=all
                if args.mode == "all" and receiver:
                    try:
                        while True:
                            time.sleep(3600)
                    except KeyboardInterrupt:
                        pass
                else:
                    return 0
            chair = JeevesChair(
                host,
                port,
                chair_queue_home,
                report_base,
                nick=args.nick,
                shops=shops,
            )
            chair.start()
            print(
                f"INFO chair nick={args.nick} host={host}:{port} shops={shops} digest={dh}",
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
