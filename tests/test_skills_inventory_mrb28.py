"""Skills overlay inventory (harvest MRB #28 fix): required SKILL.md present + frontmatter name."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"

REQUIRED = (
    "harvest",
    "jeeves-install-service",
    "jeeves-health",
    "jeeves-queue",
    "jeeves-announce-debug",
    "jeeves-worker-state",
    "jeeves-release",
    "jeeves-token-less-gate",
    "jeeves-shop-protocol",
    "jeeves-task-modes",
    "jeeves-irc-roles",
)

_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.M)


def test_required_skills_exist_with_frontmatter_name():
    missing = []
    for name in REQUIRED:
        path = ROOT / name / "SKILL.md"
        if not path.is_file():
            missing.append(f"missing:{name}")
            continue
        text = path.read_text(encoding="utf-8")
        m = _NAME.search(text)
        if not m or m.group(1) != name:
            missing.append(f"bad-name:{name}")
        # New harvest skills state overlay explicitly
        if name in ("jeeves-shop-protocol", "jeeves-task-modes", "jeeves-irc-roles"):
            assert "overlay" in text.lower()
    assert not missing, missing


def test_skills_readme_indexes_required():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in REQUIRED:
        assert name in readme, name