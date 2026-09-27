# FR: Exclusive open offer + 90s ACK window

**Date:** 2026-09-27  
**Repo:** SimonBarnett/gh-Jeeves  
**Status:** implemented (this PR)

## Problem

Jeeves offered the same `row_key` (e.g. `MRB …/gh-Jeeves#193`) to two workers
minutes apart. Root cause: `roles.py` called `assign_state.on_ack()` even when
`accept_job` returned `no_match`, which cleared the exclusive open offer while
the job remained `unaccepted`. The next `!bored` from another seat received the
same assign. Default offer timeout was also 300s (too long for “respond or
release”).

## MUST

1. At most one open offer per `row_key` (`repo|task|#n`) across all workers.
2. After an offer, wait **90 seconds** for ACK before expiring and offering that
   job again (same or other seat).
3. ACK `no_match` must **not** clear the open offer.
4. Successful accept / DONE still clear the open offer.

## Config

- Default: `DEFAULT_OFFER_TIMEOUT_S = 90`
- Optional env: `JEEVES_OFFER_TIMEOUT_S` (seconds, min 1)

## Evidence

- Live log dual-assign: `marchhare-14764` then `ionos-14020` on MRB `#193`
- Tests: `tests/test_offer_exclusive_90s.py`
