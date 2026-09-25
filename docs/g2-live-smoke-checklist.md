# G2 live smoke checklist — BobJeeves production port (FR #39)

Operator-only after deploy on **ionos**. Implementer seats do **not** hotpatch
or restart live services unless Bob assigns it.

## Pre

- [ ] **`!list` full on start** — count close to open issues and PRs (FR #49 resync-on-start)
- [ ] `gh-Jeeves` main contains `python -m jeeves` and this checklist
- [ ] Backup `~/.agentic-irc-bobiverse/queue.json` and `chair-outbox.txt`
- [ ] Backup `~/.agentic-irc-jeeves/` if present
- [ ] Confirm Ergo/BobIrcd stay untouched (no `ircd.yaml` edits)
- [ ] `GITHUB_TOKEN` / `JEEVES_GITHUB_TOKEN` (or token file) present for resync; never logged

## Install / upgrade (idempotent)

```powershell
cd <gh-Jeeves checkout>
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -DryRun -Json
# review plan.homes_distinct, never_touch_ircd, python_module=jeeves
# elevated Apply only when ready:
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -Apply -Json
```

## Singleton

- [ ] Only one `BobJeeves` service / one `python -m jeeves` holding
  `~/.agentic-irc-jeeves/.bobjeeves.singleton.json`
- [ ] Second start exits non-zero with singleton error

## Channel / webhook

- [ ] GIT announce appears only on `#bobiverse` (chair-outbox drain)
- [ ] Shop channels: no OFFER/`!bored` from Jeeves; ACK/DONE update digest
- [ ] In-channel `!list` / `!help` → **PM only** (no channel queue dump)
- [ ] `GET http://127.0.0.1:<port>/bob/v1/report` shows queue + workers
- [ ] `POST /bob/v1/git` still enqueues; 15‑minute resync does not drop concurrent webhooks

## TLS IRC (FR #46)

- [ ] BobJeeves uses **native** `python -m jeeves --tls` (`jeeves.tls_irc.TlsIrcClient`) — **not** `agentic_irc` `irc_agent --chair`
- [ ] TLS to Ergo (`irc.ntsa.uk:6697` or configured host); optional CA / pin via env
- [ ] Reconnect after disconnect uses exponential backoff on throttle ERROR (K13)
- [ ] Never edit Ergo / `ircd.yaml` during upgrade

## Queue continuity

- [ ] Pre-cutover `queue.json` unaccepted/accepted counts preserved after start
- [ ] No empty queue rewrite on first boot

## Retire old chair

- [ ] Stop old `C:\ai\agentic_irc-www` / scheduled `BobJeeves-chair` task after
  new service healthy
- [ ] Rollback: re-enable prior task; stop `BobJeeves` service; restore
  `queue.json` backup

## Record

Record date, operator, service status, sample `!list` PM, and rollback notes in
the release / FR #39 comment thread.

## FR #55 auto-join (post-merge G2)

- [ ] WHOIS Jeeves shows #bobiverse plus every current #{machine} shop
- [ ] New shop channel appears in WHOIS within one LIST poll (~60s)

## Operator note (FR #55, merged PR #59)

After deploy, confirm with `WHOIS Jeeves` that every live `#{machine}` shop plus `#bobiverse` appear. New shops appear within one re-LIST interval (default 60s). Denylist is config-only; never edit Ergo `ircd.yaml` for joins.

Code: `jeeves.channel_join.AutoJoinController` (LIST + periodic re-LIST). CAST IRON: never touch Ergo/BobIrcd (`README` rule 9; BRIEF §3 / §9).

## Operator note (FR #52, merged PR #62)

Matches README CAST IRON Modes (FR #52): authenticated `bob-*` get `+h` (`+o` in own `#{machine}` shop); authenticated `simon` from a fleet host gets `+o`. Trust is services account (SASL / account-notify / extended-join), never nick alone. Workers get no modes. No channel text. `!sweep [#chan]` is simon-only (account `simon`).

G2: check `NAMES` on `#bobiverse` and a shop (e.g. `#flamingo`) after bob/simon join — expect half-op / op prefixes. Do not edit Ergo `ircd.yaml` for modes (ChanServ AMODE / registration remains operator-owned; BRIEF: "gh-Jeeves never edits Ergo or BobIrcd config").

Code: `jeeves.mode_grants.ModeGrantController`.

