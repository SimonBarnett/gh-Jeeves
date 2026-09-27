# FR: Jeeves handoff failure monitor + ghost-busy heal

**Date:** 2026-09-27  
**Repo:** SimonBarnett/gh-Jeeves  
**Status:** implemented (this PR)

## Problem

Handoff loop failures observed on ionos:

1. **Stuck busy / missed DONE:** `ionos-14020` ACKed `MRB gh-Jeeves#202` at
   `19:30:46Z`. BobJeeves was STOP/START'd at `19:32:22Z` (nssm). The seat wrote
   `DONE` to the watch outbox, but chair had no `event=done` until a re-DONE at
   `19:35:17Z`. Meanwhile `bored_skip reason=busy` at `19:34:39Z` and **FR #204**
   was assigned to `marchhare-14764`.
2. **Ghost busy:** resync can drop an accepted row when GitHub no longer lists
   the job (merged/closed) while the seat is still connected, without clearing
   `queue.workers[nick].state=busy`. `decide()` then forever `bored_skip`.
3. Dual-offer was fixed in PR #196 (merged); monitor still watches for regressions.

## MUST

1. Deterministic local monitor under `tools/watch_jeeves_handoff.py` that detects
   ghost busy, stale busy, ACK-without-DONE, dual assign/offer, `ack_no_match`
   storms, `bored_skip reason=busy`, and outbox DONE lag vs chair log.
2. Alerts only to a local file (`~/.agentic-irc-jeeves/handoff-alerts.log`) —
   **no IRC spam**.
3. Chair heal: `decide()` clears busy when there is no accepted row for the nick.
4. Resync: when a connected nick's accepted job is gone from GitHub, idle the
   worker as well as dropping the accepted row.
5. Tests for heal + monitor scanners.

## How to run the monitor

```powershell
cd C:\ai\gh-Jeeves
python tools/watch_jeeves_handoff.py --once
python tools/watch_jeeves_handoff.py --once --json
python tools/watch_jeeves_handoff.py --loop 30 --quiet-ok
# optional wrapper
powershell -File tools/Watch-JeevesHandoff.ps1 -Once
```

Alert log: `%USERPROFILE%\.agentic-irc-jeeves\handoff-alerts.log`.

## Evidence (service log)

```
2026-09-27T19:30:46Z event=ack nick=ionos-14020 job=SimonBarnett/gh-Jeeves#202 mode=MRB
2026-09-27T19:32:22Z nssm STOP BobJeeves / 19:32:25 START
2026-09-27T19:34:39Z event=bored_skip nick=ionos-14020 action=busy reason=busy
2026-09-27T19:34:49Z event=assign nick=marchhare-14764 … FR …#204
2026-09-27T19:35:17Z event=done nick=ionos-14020 job=SimonBarnett/gh-Jeeves#202 mode=MRB
```
