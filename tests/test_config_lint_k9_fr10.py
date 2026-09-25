"""FR #10 / K9: bobiverse.json must not have duplicate reportUrl keys.

Seed outcome: a single reportUrl key plus a config lint test.
Failing-test-first: encode the duplicate-key fixture before the lint helper exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "bobiverse_dup_reporturl.json"
# Live fleet registry (agentic_build) — lint when present beside this workspace.
AGENTIC_BUILD_CFG = ROOT.parent / "agentic_build" / "config" / "bobiverse.json"
CANONICAL_REPORT_URL = "https://irc.ntsa.uk/bob/v1/report"


def test_fixture_raw_text_has_two_reporturl_keys():
    """Evidence shape from brief §4.2: two reportUrl lines in the file text."""
    text = FIXTURE.read_text(encoding="utf-8")
    assert text.count('"reportUrl"') == 2
    assert "irc.ntsa.uk:7700" in text
    assert CANONICAL_REPORT_URL in text


def test_lint_rejects_duplicate_reporturl_keys():
    from jeeves.config_lint import find_duplicate_json_keys, lint_bobiverse_config

    dups = find_duplicate_json_keys(FIXTURE.read_text(encoding="utf-8"))
    assert "reportUrl" in dups
    issues = lint_bobiverse_config(FIXTURE)
    assert any("reportUrl" in i for i in issues)


def test_lint_accepts_single_canonical_reporturl(tmp_path: Path):
    from jeeves.config_lint import lint_bobiverse_config

    good = tmp_path / "bobiverse.json"
    good.write_text(
        "{\n"
        '  "channel": "#bobiverse",\n'
        f'  "reportUrl": "{CANONICAL_REPORT_URL}",\n'
        '  "reportPort": 7700,\n'
        '  "chairNick": "Jeeves"\n'
        "}\n",
        encoding="utf-8",
    )
    assert lint_bobiverse_config(good) == []


@pytest.mark.skipif(not AGENTIC_BUILD_CFG.is_file(), reason="agentic_build checkout not beside gh-Jeeves")
def test_agentic_build_bobiverse_has_single_reporturl():
    """Live config must be clean (K9 fix)."""
    from jeeves.config_lint import find_duplicate_json_keys, lint_bobiverse_config

    text = AGENTIC_BUILD_CFG.read_text(encoding="utf-8")
    assert text.count('"reportUrl"') == 1
    assert find_duplicate_json_keys(text) == []
    assert lint_bobiverse_config(AGENTIC_BUILD_CFG) == []
    # Canonical HTTPS without :7700 (IIS); reportPort may still document 7700 locally.
    assert CANONICAL_REPORT_URL in text
    assert "irc.ntsa.uk:7700/bob/v1/report" not in text
