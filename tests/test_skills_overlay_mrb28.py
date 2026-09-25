"""Skills overlay integrity (harvest MRB #28 rebase): no secrets, seed FRs filled, index matches dirs."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"
SECRET = re.compile(r"ghp_[A-Za-z0-9]+|sk-[A-Za-z0-9]{20,}|BEGIN (RSA |OPENSSH )?PRIVATE")
TODO_SEED = re.compile(r"TODO:\s*seed FR", re.I)


def test_skills_index_and_no_secrets():
    assert ROOT.is_dir()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dirs = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
    assert "jeeves-shop-protocol" in dirs
    assert "jeeves-task-modes" in dirs
    assert "jeeves-irc-roles" in dirs
    assert "harvest" in dirs
    for name in dirs:
        assert name in readme or f"[{name}]" in readme or f"]({name}/" in readme
        text = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        assert not SECRET.search(text), name
        assert not TODO_SEED.search(text), name
        assert "name:" in text[:200]
        assert text.lstrip().startswith("---")


def test_install_and_queue_keep_live_frs():
    inst = (ROOT / "jeeves-install-service" / "SKILL.md").read_text(encoding="utf-8")
    assert "FR #48" in inst
    assert "FR #71" in inst or "chair-outbox.pos" in inst
    assert "BobIrcd" in inst
    q = (ROOT / "jeeves-queue" / "SKILL.md").read_text(encoding="utf-8")
    assert "FR #75" in q or "ignored.json" in q
