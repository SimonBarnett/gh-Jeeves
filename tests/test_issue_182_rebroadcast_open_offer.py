"""FR #182: repeat !bored must rebroadcast open offer; worker nick _suffix ok."""
from __future__ import annotations

import json
import time
from pathlib import Path

from jeeves.assign import ChairAssignState, format_assign_line
from jeeves.nicks import (
    canonical_worker_nick,
    is_worker_nick,
    nick_matches_shop,
    parse_worker_nick,
)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "bobiverse"
    home.mkdir()
    (home / "queue.json").write_text(
        json.dumps(
            {
                "v": 1,
                "unaccepted": [
                    {
                        "task": "FR",
                        "repo": "SimonBarnett/gh-Jeeves",
                        "id": "#179",
                        "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/179",
                        "seq": 1,
                        "line": "harvest intake",
                    }
                ],
                "accepted": [],
                "done": [],
                "workers": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (home / "focus.json").write_text(
        json.dumps({"v": 1, "strict": False, "repos": {}, "items": {}}) + "\n",
        encoding="utf-8",
    )
    (home / "ignored.json").write_text(
        json.dumps({"v": 1, "repos": [], "items": []}) + "\n", encoding="utf-8"
    )
    return home


def test_parse_worker_nick_strips_listen_suffix():
    assert parse_worker_nick("marchhare-36340_l") == ("marchhare", "36340")
    assert is_worker_nick("marchhare-36340_l")
    assert canonical_worker_nick("marchhare-36340_l") == "marchhare-36340"
    assert nick_matches_shop("marchhare-36340_l", "#marchhare")


def test_repeat_bored_rebroadcasts_open_offer(tmp_path: Path):
    home = _home(tmp_path)
    st = ChairAssignState(timeout_s=600.0)
    st.bind(home)
    now = time.time()
    d1 = st.decide(home, "ionos-12916", "#ionos", live_nicks={"ionos-12916"}, now=now)
    assert d1.action == "assign"
    assert d1.line and "gh-Jeeves#179" in d1.line
    # Second !bored while offer still open: same line, action open (not silent).
    d2 = st.decide(
        home, "ionos-12916", "#ionos", live_nicks={"ionos-12916"}, now=now + 10
    )
    assert d2.action == "open"
    assert d2.reason == "one_open_offer"
    assert d2.line == d1.line


def test_format_assign_line_stable():
    row = {
        "task": "FR",
        "repo": "SimonBarnett/gh-Jeeves",
        "id": "#179",
        "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/179",
    }
    line = format_assign_line("ionos-1", row)
    assert line.startswith("ionos-1: FR SimonBarnett/gh-Jeeves#179 ")
