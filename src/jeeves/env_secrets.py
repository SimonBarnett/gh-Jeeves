"""FR #48: load IRC/SASL secrets from *_PASSWORD_FILE env (nssm path-only)."""

from __future__ import annotations

import os
from pathlib import Path


def read_secret_file(env_key: str) -> str:
    """First line of a password file referenced by env. Never log the value."""
    path = (os.environ.get(env_key) or "").strip()
    if not path:
        return ""
    try:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").splitlines()[0].strip()
    except OSError:
        return ""
    return ""


def hydrate_secrets_from_files() -> None:
    """Copy file secrets into AGENTIC_IRC_SASL_PASSWORD / AGENTIC_IRC_PASSWORD.

    Install-BobJeeves sets AGENTIC_IRC_SASL_PASSWORD_FILE on the service;
    argparse and TlsIrcClient read the non-_FILE env vars.
    """
    if not (os.environ.get("AGENTIC_IRC_SASL_PASSWORD") or "").strip():
        v = read_secret_file("AGENTIC_IRC_SASL_PASSWORD_FILE")
        if v:
            os.environ["AGENTIC_IRC_SASL_PASSWORD"] = v
    if not (os.environ.get("AGENTIC_IRC_PASSWORD") or "").strip():
        v = read_secret_file("AGENTIC_IRC_PASSWORD_FILE")
        if v:
            os.environ["AGENTIC_IRC_PASSWORD"] = v
