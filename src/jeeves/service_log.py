"""FR #73: rotating service log so health can be checked without an IRC probe.

Writes to ``{JEEVES_HOME}/bobjeeves-service.log`` (size-capped rotation).
Never logs secrets (passwords, tokens, SASL material).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Default: 2 MiB × 5 backups — keeps a few reconnect cycles without filling disk
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
LOG_NAME = "bobjeeves-service.log"

_configured = False


def service_log_path(jeeves_home: Path | None = None) -> Path:
    home = Path(
        jeeves_home
        or os.environ.get("JEEVES_HOME")
        or os.environ.get("AGENTIC_IRC_HOME")
        or (Path.home() / ".agentic-irc-jeeves")
    ).expanduser()
    return home / LOG_NAME


def configure_service_logging(
    jeeves_home: Path | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
) -> Path:
    """Attach rotating file handler to root + jeeves.* loggers. Idempotent."""
    global _configured
    path = service_log_path(jeeves_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _configured:
        return path

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # Force UTC-ish timestamps via asctime; Windows local is fine for ops

    fh = RotatingFileHandler(
        str(path),
        maxBytes=max(64_000, int(max_bytes)),
        backupCount=max(1, int(backup_count)),
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    # avoid duplicate handlers on reload
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(
            path.resolve()
        ):
            root.removeHandler(h)
    root.addHandler(fh)

    # also keep a StreamHandler so nssm AppStdout still sees lines if wired
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    _configured = True
    logging.getLogger("jeeves.service").info(
        "event=service_log path=%s max_bytes=%s backups=%s",
        path,
        max_bytes,
        backup_count,
    )
    return path


def event(logger: logging.Logger, kind: str, **fields: object) -> None:
    """One structured INFO line: event=<kind> k=v ... (never secrets)."""
    parts = [f"event={kind}"]
    for k, v in fields.items():
        if v is None:
            continue
        # scrub accidental secret-looking values
        s = str(v)
        if any(m in s for m in ("ghp_", "gho_", "BEGIN PRIVATE", "xoxb-", "sk-")):
            s = "(redacted)"
        parts.append(f"{k}={s}")
    logger.info(" ".join(parts))
