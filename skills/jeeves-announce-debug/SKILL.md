---
name: jeeves-announce-debug
description: >
  Debug a GitHub delivery that was not announced: hook deliveries, receiver
  secret-field filter, chair-outbox, Ergo 417 long lines. Use when GIT line
  missing on #bobiverse, ping ok but issues silent, or /jeeves-announce-debug.
---

# jeeves-announce-debug

## Purpose

Trace GitHub → receiver → outbox → Jeeves → `#bobiverse` when a delivery is
missing. Covers hook deliveries, secret-field filter (agentic_irc #206),
chair-outbox drain, and 417 line splits (#205).

Agentic control is an overlay: the token-less path must never depend on this skill.

## FR #24 — length-safe announcements

Machine-read lines use `src/gh_jeeves/announce.py`:

- Vital fields first (event, task, `owner/repo#n`, action, URL, fixes, head); title last and truncated only.
- Byte budget = IRC 512 − (`:nick!user@host PRIVMSG #chan :` + CRLF).
- Pre-send `validate_round_trip` / `prepare_send`; failures write `queue_events/` (queue from webhook, not IRC).
- Simulated 417 → `handle_simulated_417` log + queue event.
- Tests: `pytest tests/test_announce_fr24.py` (token-less; part of gate surface).

## Checklist (delivery missing)

1. GitHub hook delivery → 2xx on `/bob/v1/git`?
2. Secret-field filter (K14 / agentic_irc #206) — body text must not reject whole payload.
3. `chair-outbox` / announce body under budget (`utf8_len(body) <= body_budget()`).
4. Ergo 417 → check logs for `ERROR IRC 417`; queue event file still present.
5. Never continue a vital field onto a second IRC line.
