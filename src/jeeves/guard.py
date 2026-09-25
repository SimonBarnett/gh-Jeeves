"""No-LLM process guard for G1: unset AI env; block non-loopback HTTP."""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

AI_ENV_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "CURSOR_API_KEY",
    "GITHUB_COPILOT_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def scrub_ai_env() -> dict[str, str | None]:
    """Unset known AI keys; return previous values for restore."""
    prev: dict[str, str | None] = {}
    for k in AI_ENV_KEYS:
        prev[k] = os.environ.pop(k, None)
    os.environ["JEEVES_G1_NO_LLM"] = "1"
    return prev


def restore_env(prev: dict[str, str | None]) -> None:
    os.environ.pop("JEEVES_G1_NO_LLM", None)
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def is_loopback_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


class NonLoopbackBlocked(RuntimeError):
    pass


_orig_create_connection = socket.create_connection


def _guarded_create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    h = str(host).lower()
    if h not in ("127.0.0.1", "localhost", "::1"):
        # allow DNS to localhost names already covered; block public
        try:
            infos = socket.getaddrinfo(h, None)
            addrs = {i[4][0] for i in infos}
            if not addrs.issubset({"127.0.0.1", "::1"}):
                raise NonLoopbackBlocked(f"G1 guard blocked connect to {host!r}")
        except NonLoopbackBlocked:
            raise
        except OSError:
            raise NonLoopbackBlocked(f"G1 guard blocked connect to {host!r}")
    return _orig_create_connection(address, *args, **kwargs)


@contextmanager
def no_llm_network_guard() -> Iterator[None]:
    """Patch socket.create_connection to allow loopback only."""
    prev = scrub_ai_env()
    socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.create_connection = _orig_create_connection  # type: ignore[assignment]
        restore_env(prev)
