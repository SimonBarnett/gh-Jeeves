"""agentic_build #456: digest merge keeps per-pool cursor_pools + sand_period_end."""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import apply_report, coerce_machine, load_digest, public_digest_snapshot


def test_coerce_machine_keeps_sand_and_pools():
    sand = "2026-09-30T17:23:58.025Z"
    bill = "2026-10-16T17:23:01Z"
    m = coerce_machine(
        "flamingo",
        {
            "online": True,
            "sand_period_end": sand,
            "cursor_period_end": bill,
            "cursor_pools": [
                {
                    "group_id": "grok-chat",
                    "remaining_pct": 23,
                    "period_end": sand,
                },
                {
                    "group_id": "auto",
                    "remaining_pct": 82,
                    "period_end": bill,
                },
            ],
            "pcent": {"grok-chat": 23, "cursor-models": 82},
        },
    )
    assert m["sand_period_end"] == sand
    assert m["cursor_period_end"] == bill
    assert isinstance(m["cursor_pools"], list)
    assert m["cursor_pools"][0]["group_id"] == "grok-chat"
    assert m["cursor_pools"][0]["period_end"] == sand
    assert m["cursor_pools"][1]["period_end"] == bill


def test_merge_op_persists_pools_on_get(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    sand = "2026-09-30T17:23:58.025Z"
    bill = "2026-10-16T17:23:01Z"
    pools = [
        {
            "group_id": "grok-chat",
            "group_label": "grok chat",
            "remaining_pct": 23,
            "period_end": sand,
        },
        {
            "group_id": "high-cost-models",
            "group_label": "high cost models",
            "remaining_pct": 100,
            "period_end": bill,
        },
        {
            "group_id": "auto",
            "group_label": "Low cost models",
            "remaining_pct": 82,
            "period_end": bill,
        },
    ]
    out = apply_report(
        home,
        {
            "op": "merge",
            "machine": "flamingo",
            "online": True,
            "status": "operational",
            "sand_period_end": sand,
            "cursor_period_end": bill,
            "cursor_pools": pools,
            "pcent": {
                "grok-chat": 23,
                "high-cost-models": 100,
                "cursor-models": 82,
            },
        },
    )
    assert out.ok
    doc = load_digest(home)
    fl = doc["machines"]["flamingo"]
    assert fl["sand_period_end"] == sand
    assert fl["cursor_period_end"] == bill
    assert len(fl["cursor_pools"]) == 3
    by_id = {p["group_id"]: p for p in fl["cursor_pools"]}
    assert by_id["grok-chat"]["period_end"] == sand
    assert by_id["auto"]["period_end"] == bill
    # Top-level cursor_pools still refreshed from last merge (TipForm fan-in).
    assert isinstance(doc.get("cursor_pools"), list)
    assert len(doc["cursor_pools"]) == 3
    snap = public_digest_snapshot(home)
    assert snap["machines"]["flamingo"]["sand_period_end"] == sand
    assert snap["machines"]["flamingo"]["cursor_pools"][0]["group_id"] == "grok-chat"
