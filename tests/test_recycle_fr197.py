"""FR #197: !recycle documented by Jeeves; executed by local bob seat."""
from __future__ import annotations

import inspect
from pathlib import Path

from jeeves.cast_iron import is_recycle_egress, shop_egress_allowed_for_chair
from jeeves.commands import get_command, validate_registry
from jeeves.helpcmd import HELP_DETAIL_MAX_LINES, build_help
from jeeves.recycle import (
    RECYCLE_STEPS,
    RecycleGate,
    authorize_recycle,
    decide_recycle,
    format_recycle_route,
    is_recycle,
    parse_recycle,
    recycle_steps,
)
from jeeves.roles import JeevesChair


def test_registry_lists_recycle():
    assert validate_registry() == []
    spec = get_command("recycle")
    assert spec is not None
    assert "!recycle" in spec.syntax
    assert "local bob" in spec.summary.lower() or "bob seat" in spec.details.lower()
    assert "simon" in spec.roles or "bob" in spec.roles


def test_help_includes_recycle_for_ops_not_workers():
    simon = "\n".join(build_help("simon", include_shop_pointer=False).lines)
    assert "recycle" in simon.lower()
    bob = "\n".join(build_help("bob-ionos", include_shop_pointer=False).lines)
    assert "recycle" in bob.lower()
    worker = "\n".join(build_help("marchhare-14764", include_shop_pointer=False).lines)
    assert not any(ln.strip().lower().startswith("!recycle") for ln in worker.splitlines())


def test_help_recycle_detail_max_5():
    r = build_help("simon", "recycle", include_shop_pointer=False)
    assert len(r.lines) <= HELP_DETAIL_MAX_LINES
    joined = "\n".join(r.lines).lower()
    assert "local" in joined
    assert "bob" in joined


def test_parse_and_steps():
    assert parse_recycle("!recycle") == (True, None)
    assert parse_recycle("!RECYCLE all") == (True, "all")
    assert is_recycle("!recycle marchhare")
    assert not is_recycle("!bored")
    steps = recycle_steps()
    ids = [s["id"] for s in steps]
    assert ids == list(RECYCLE_STEPS)
    assert "git_ff_only" in ids
    assert "cleanup_owned_orphans" in ids


def test_authorize_requires_account():
    assert authorize_recycle(nick="simon", account="simon", owner_account="simon")[0]
    assert not authorize_recycle(nick="simon", account=None, owner_account="simon")[0]
    assert not authorize_recycle(nick="simon", account="other", owner_account="simon")[0]
    assert not authorize_recycle(nick="random", account="random", owner_account="simon")[0]
    assert authorize_recycle(nick="bob-ionos", account="bobops", owner_account="simon")[0]
    assert not authorize_recycle(nick="bob-ionos", account=None, owner_account="simon")[0]


def test_decide_routes_and_cooldown():
    gate = RecycleGate(cooldown_s=60)
    now = 1_000_000.0
    d = decide_recycle(
        nick="simon",
        account="simon",
        channel="#marchhare",
        arg=None,
        owner_account="simon",
        gate=gate,
        now=now,
    )
    assert d.ok
    assert d.channel_line.startswith("RECYCLE machine=marchhare")
    assert "exec=local-bob-seat" in d.channel_line
    assert shop_egress_allowed_for_chair(d.channel_line)
    assert is_recycle_egress(d.channel_line)

    d2 = decide_recycle(
        nick="simon",
        account="simon",
        channel="#marchhare",
        arg=None,
        owner_account="simon",
        gate=gate,
        now=now + 5,
    )
    assert not d2.ok
    assert d2.reason == "cooldown"


def test_denied_no_channel_route():
    d = decide_recycle(
        nick="evil",
        account="evil",
        channel="#marchhare",
        arg=None,
        owner_account="simon",
    )
    assert not d.ok
    assert not d.channel_line


def test_chair_handler_no_host_ops_in_source():
    src = inspect.getsource(JeevesChair._handle_recycle)
    for banned in ("Stop-Process", "os.kill", "subprocess", "git fetch", "Restart-Service"):
        assert banned not in src


def test_format_route_stable():
    line = format_recycle_route(machine="#ionos", requester="simon", scope="local")
    assert line == "RECYCLE machine=ionos by=simon scope=local exec=local-bob-seat"


def test_bob_seat_recycle_script_covers_ordered_steps():
    """Local executor lives beside Jeeves; bob seat invokes it on RECYCLE wire."""
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "Invoke-BobSeatRecycle.ps1"
    assert script.is_file(), "tools/Invoke-BobSeatRecycle.ps1 required for FR #197 local exec"
    text = script.read_text(encoding="utf-8")
    for step in RECYCLE_STEPS:
        assert step in text, f"script missing step id {step}"
    # Safety: must not kill bare powershell/node/python without ownership markers
    assert "Test-OwnedWorkerProcess" in text
    assert "Watch-Bob" in text or "agentic" in text
    assert "git merge --ff-only" in text or "merge --ff-only" in text
    assert "dirty" in text.lower()
    # Scope check for wrong machine
    assert "ExpectedMachine" in text
