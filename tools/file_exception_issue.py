#!/usr/bin/env python3
"""FR #148: deterministic exception → GitHub issue (scripts only, no LLM).

Usage:
  python tools/file_exception_issue.py --test-raise
  python tools/file_exception_issue.py --drain
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jeeves.exception_report import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
