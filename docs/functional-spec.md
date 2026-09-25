# Functional specification: gh-Jeeves

Pulled from `docs/vision.md` LOCKED items and `docs/brief/JEEVES_BRIEF.md`.

## Product

Jeeves is the Bob Fleet **deterministic GIT chair**: a Windows service that announces GitHub webhook events as `GIT …` lines on `#bobiverse`, owns the task queue on the digest webhook, and silently records worker `ACK`/`DONE` in every `#{machine}` shop channel. No LLM on that path.

## Success gate (required)

**S1 — Token-less end-to-end:** with every LLM/token pool disabled, the chain GitHub event → Jeeves announce + queue → worker `!bored` → ear offer → `ACK` → accepted+busy → worker work → `DONE` → done+idle+supersede must complete with scripts only.

- **G1** (CI, every PR): local test ircd + stub receiver + scripted fake worker; process guard proves no LLM/token network calls.
- **G2** (manual after deploy): live smoke on a sandbox repo; procedure in `docs/migration-plan.md` §16.3.

A release is not shippable unless G1 is green and G2 is recorded.

## CAST IRON

1. Announce GIT work only on `#bobiverse`; queue on digest webhook `/bob/v1/report`.
2. Silent shop listener: `ACK` → accepted + busy; `DONE` → done + idle + supersede.
3. Never handle `!bored`; never offer or assign.
4. Workers stay in their own `#{machine}`; idle > 2 min → `!bored`; ear offers top unaccepted addressed to that nick.
5. Announce → accepted-worker path is fully deterministic (scripts only; works during token outage).
6. Supersede: FR+PR → MRB; MRB PASS merged → UAT; PR closed unmerged → restore FR; issue closed → remove; issue reopened → re-add FR.
7. MRB process: PASS merges and closes FR; FAIL one fix PR, merge both, FR stays open; only Bob stamps UAT.
8. `!list` by PM returns unaccepted queue by PM.
9. Ergo ops: `bob-{machine}` op in `#{machine}`; `Jeeves` op in `#bobiverse`; simon ops only via fleet client cert (Ergo config owned elsewhere).
10. Jeeves runs as its own Windows service.
11. Worker busy/idle observable from ACK/DONE; wakes serialised; never mistaken for idle while a hidden run works.

## Scope (owns)

1. Announce chair (TLS IRC client, reconnect backoff, singleton nick, 417-safe split).
2. Queue engine with supersede reducer; `queue.json` crash mirror; webhook source of truth.
3. Shop listener for ACK/DONE/NACK/GIVEUP; silent; never !bored/offers.
4. `!list` PM replies (capped, rate-limited).
5. Webhook writer for accepted/done/busy/idle.
6. GitHub receiver `/bob/v1/git` including secret-field filter fix.
7. Service installer `BobJeeves` (never touch Ergo/BobIrcd).
8. G1 local-ircd tests; docs; `skills/` agent overlay.

## Non-goals

LLM features, offering/assigning, Ergo/BobIrcd config, TipForm UI, worker implementation.

## Wire grammar (owned here)

- Announce: `GIT <event> <owner/repo> …` (≤380 chars).
- Idle: `!bored` (worker → own shop).
- Offer (ear): `<nick>: OFFER <FR|MRB|UAT> <owner/repo>#<n> <url>` — one open offer per worker.
- Accept: `ACK <TYPE> <owner/repo>#<n>`
- Complete: `DONE <TYPE> <owner/repo>#<n> <result> <url>`
- Return: `NACK|GIVEUP <TYPE> <owner/repo>#<n>`
- List: `/msg Jeeves !list`
- Nicks: `{machine}-{pid}` and legacy `w-<short>-<pid>` in own shop only.

## Skills overlay

`skills/` lets agents operate and diagnose Jeeves. Agentic control is an **overlay**: the token-less path must never depend on a skill or LLM. Foundation: `skills/harvest` (honesty box → this repo).

## Related upstream FRs

See brief section 13: agentic_irc #205–#211, agentic_build #327–#330, AgentMonitor #87–#91.
