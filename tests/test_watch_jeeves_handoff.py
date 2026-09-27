"""Unit tests for tools/watch_jeeves_handoff.py (no IRC, no network)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = str(_REPO / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from watch_jeeves_handoff import (  # noqa: E402
    run_scan,
    scan_offers_dual,
    scan_outbox_vs_chair,
    scan_queue_state,
    scan_service_log,
)


def test_ghost_busy_and_dual_offer(tmp_path: Path):
    digest = tmp_path / "digest"
    digest.mkdir()
    (digest / "queue.json").write_text(
        json.dumps(
            {
                "v": 1,
                "unaccepted": [
                    {
                        "repo": "SimonBarnett/gh-Jeeves",
                        "task": "FR",
                        "id": "#204",
                    }
                ],
                "accepted": [],
                "done": [],
                "workers": {
                    "ionos-14020": {
                        "state": "busy",
                        "ts": "2026-09-27T19:30:46Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (digest / "offers.json").write_text(
        json.dumps(
            {
                "v": 1,
                "open": {
                    "ionos-1": {
                        "offered_at": 9999999999,
                        "row": {
                            "repo": "SimonBarnett/gh-Jeeves",
                            "task": "MRB",
                            "id": "#193",
                        },
                    },
                    "marchhare-1": {
                        "offered_at": 9999999999,
                        "row": {
                            "repo": "SimonBarnett/gh-Jeeves",
                            "task": "MRB",
                            "id": "#193",
                        },
                    },
                },
                "attempts": {},
            }
        ),
        encoding="utf-8",
    )
    qf = scan_queue_state(digest, busy_max_s=3600)
    kinds = {f.kind for f in qf}
    assert "ghost_busy" in kinds
    assert "busy_blocks_eligible" in kinds
    df = scan_offers_dual(digest, offer_window_s=90)
    assert any(f.kind == "dual_offer" for f in df)


def test_ack_without_done_and_outbox_lag(tmp_path: Path):
    log = tmp_path / "bobjeeves-service.log"
    log.write_text(
        "2026-09-27T10:00:00Z INFO jeeves.chair event=ack nick=ionos-14020 "
        "job=SimonBarnett/gh-Jeeves#202 mode=MRB\n"
        "2026-09-27T10:12:00Z INFO jeeves.chair event=bored_skip nick=ionos-14020 "
        "action=busy reason=busy\n",
        encoding="utf-8",
    )
    # now = 10:15 → ACK age 15m (>60s), bored_skip age 3m (<15m window)
    now = datetime(2026, 9, 27, 10, 15, 0, tzinfo=timezone.utc).timestamp()
    findings = scan_service_log(
        log, ack_done_timeout_s=60, ack_no_match_storm=5, now=now
    )
    assert any(f.kind == "ack_without_done" for f in findings)
    assert any(f.kind == "bored_skip_busy" for f in findings)

    ob = tmp_path / "outbox.txt"
    ob.write_text(
        "DONE MRB SimonBarnett/gh-Jeeves#202 PASS "
        "https://github.com/SimonBarnett/gh-Jeeves/pull/202\n",
        encoding="utf-8",
    )
    os.utime(ob, (now - 45, now - 45))
    lag = scan_outbox_vs_chair([ob], log, lag_s=30, now=now)
    assert any(f.kind == "outbox_done_lag" for f in lag)


def test_run_scan_ok_empty(tmp_path: Path):
    digest = tmp_path / "digest"
    jh = tmp_path / "jeeves"
    digest.mkdir()
    jh.mkdir()
    (digest / "queue.json").write_text(
        '{"v":1,"unaccepted":[],"accepted":[],"done":[],"workers":{}}\n',
        encoding="utf-8",
    )
    (digest / "offers.json").write_text(
        '{"v":1,"open":{},"attempts":{}}\n', encoding="utf-8"
    )
    log = jh / "bobjeeves-service.log"
    log.write_text("", encoding="utf-8")
    result = run_scan(
        digest_home=digest,
        jeeves_home=jh,
        log_path=log,
        busy_max_s=3600,
        ack_done_timeout_s=1200,
        ack_no_match_storm=5,
        outbox_lag_s=30,
        include_outboxes=False,
    )
    assert result.findings == []


def test_healthy_busy_with_accepted_is_not_ghost(tmp_path: Path):
    digest = tmp_path / "digest"
    digest.mkdir()
    (digest / "queue.json").write_text(
        json.dumps(
            {
                "v": 1,
                "unaccepted": [
                    {
                        "repo": "SimonBarnett/gh-Jeeves",
                        "task": "FR",
                        "id": "#204",
                    }
                ],
                "accepted": [
                    {
                        "repo": "SimonBarnett/gh-Jeeves",
                        "task": "MRB",
                        "id": "#206",
                        "nick": "ionos-14020",
                    }
                ],
                "done": [],
                "workers": {
                    "ionos-14020": {
                        "state": "busy",
                        "ts": "2026-09-27T19:47:48Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    findings = scan_queue_state(digest, busy_max_s=3600)
    kinds = {f.kind for f in findings}
    assert "ghost_busy" not in kinds


def test_dual_assign_log_and_ack_no_match_storm(tmp_path: Path):
    log = tmp_path / "bobjeeves-service.log"
    # Two assigns of same row within window + many ack_no_match
    lines = [
        "2026-09-27T19:23:58Z INFO jeeves.chair event=assign nick=marchhare-14764 "
        "line=marchhare-14764: MRB SimonBarnett/gh-Jeeves#193 "
        "https://github.com/SimonBarnett/gh-Jeeves/pull/193\n",
        "2026-09-27T19:26:09Z INFO jeeves.chair event=assign nick=ionos-14020 "
        "line=ionos-14020: MRB SimonBarnett/gh-Jeeves#193 "
        "https://github.com/SimonBarnett/gh-Jeeves/pull/193\n",
    ]
    for i in range(6):
        lines.append(
            f"2026-09-27T19:27:0{i}Z WARNING jeeves.chair event=ack_no_match "
            f"nick=ionos-14020 repo=SimonBarnett/gh-Jeeves#19{i} task=MRB "
            "reason=no_queue_row\n"
        )
    log.write_text("".join(lines), encoding="utf-8")
    now = datetime(2026, 9, 27, 19, 28, 0, tzinfo=timezone.utc).timestamp()
    findings = scan_service_log(
        log, ack_done_timeout_s=1200, ack_no_match_storm=5, now=now
    )
    kinds = {f.kind for f in findings}
    assert "dual_assign_log" in kinds
    assert "ack_no_match_storm" in kinds


def test_outbox_done_matched_in_log_is_not_lag(tmp_path: Path):
    log = tmp_path / "bobjeeves-service.log"
    log.write_text(
        "2026-09-27T19:35:17Z INFO jeeves.chair event=done nick=ionos-14020 "
        "job=SimonBarnett/gh-Jeeves#202 mode=MRB\n",
        encoding="utf-8",
    )
    ob = tmp_path / "outbox.txt"
    ob.write_text(
        "DONE MRB SimonBarnett/gh-Jeeves#202 PASS "
        "https://github.com/SimonBarnett/gh-Jeeves/pull/202\n",
        encoding="utf-8",
    )
    now = datetime(2026, 9, 27, 19, 36, 0, tzinfo=timezone.utc).timestamp()
    os.utime(ob, (now - 10, now - 10))
    lag = scan_outbox_vs_chair([ob], log, lag_s=30, now=now)
    assert lag == []
