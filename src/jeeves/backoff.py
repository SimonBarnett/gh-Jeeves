"""IRC reconnect backoff (K13 / FR #14 / FR #46): exponential + jitter on throttle ERROR.

Shared policy for Jeeves (`jeeves.tls_irc`) and ears: same `is_throttle_error` /
`throttle_delay_s` (cap ~30s on reconnect). agentic_irc ears should call the same
helpers or mirror this formula.
"""
from __future__ import annotations

import random
import re

_THROTTLE_RE = re.compile(
    r"(?i)(too many (times|connections)|throttl|try again|connection (limit|refused))",
)


def is_throttle_error(text: str) -> bool:
    return bool(_THROTTLE_RE.search(text or ""))


def throttle_delay_s(
    attempt: int,
    *,
    base: float = 2.0,
    cap: float = 300.0,
    jitter: float = 0.25,
    rng: random.Random | None = None,
) -> float:
    """
    Seconds to wait after the n-th consecutive throttle/disconnect (attempt >= 1).

    delay = min(cap, base * 2**(attempt-1)) * (1 +/- jitter)
    """
    n = max(1, int(attempt))
    raw = min(float(cap), float(base) * (2 ** (n - 1)))
    r = rng or random.Random()
    factor = 1.0 + r.uniform(-abs(jitter), abs(jitter))
    return max(0.5, raw * factor)
