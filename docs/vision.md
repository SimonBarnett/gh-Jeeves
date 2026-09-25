# Vision: gh-Jeeves

## Objective

The Bob Fleet GIT chair is a deterministic Windows service that processes GitHub events through to worker ACK/DONE and queue supersede with scripts only, with no LLM or token pool required on that path.

LOCKED

## Success

| id | metric | target | how measured | fail-when |
|----|--------|--------|--------------|-----------|
| S1 | Token-less end-to-end GIT chain (section 0 gate) | G1 green on every PR; G2 recorded before each release | `pytest tests/g1_token_less_e2e/` against local test ircd with AI env unset and outbound HTTP except loopback failing the test; G2 procedure in `docs/migration-plan.md` §16.3 recorded in release notes | Any G1 step misses its wire line or state snapshot; any outbound LLM/token call; release without G1 green + G2 recorded |
| S2 | CAST IRON: Jeeves never handles !bored or offers | 0 shop claim posts from Jeeves nick | `pytest tests/cast_iron/` asserts chair has no !bored handler and no OFFER emit | Jeeves posts a claim/offer in any #{machine} |
| S3 | ACK marks accepted + busy; DONE marks done + idle + supersede | 100% of scripted ACK/DONE update queue and digest | unit + integration tests on queue reducer and webhook writer | accepted stays empty after ACK; busy/idle not written |

## Shape

Primary: service

Hybrid note: product is the Jeeves Windows service plus its queue/receiver scripts. Docs and `skills/` are delivery overlays for agents; TipForm and ears stay elsewhere.

LOCKED

## Stack

Default: Python 3 (chair, queue, receiver) + PowerShell installers (NSSM Windows service) on Windows Server / ionos, public GitHub `SimonBarnett/gh-Jeeves`, private Ergo TLS IRC (`irc.ntsa.uk:6697`).

Why: matches the live chair extracted from `agentic_irc` (`irc_agent.py --chair`, `gitclaim.py`, `bobreport` / `bobcallback`), already runs on ionos, and keeps the token-less path as plain scripts.

Why-not: Node/Go rewrite (no fleet payoff); LLM chair (breaks CAST IRON and S1); shipping inside `agentic_irc` forever (blocks independent release, service, and G1 gate).

LOCKED

## Architecture

Who talks to what (Phase 0):

```
GitHub --POST /bob/v1/git--> IIS --> receiver (loopback)
                                      |
                                      +--> queue reducer + chair-outbox
                                      |
Jeeves service <--- drain outbox -----+
   | joins #bobiverse (announce GIT lines)
   | silent in every #{machine} (ACK/DONE -> accepted/busy, done/idle)
   +--> POST /bob/v1/report (queue + worker state)

bob-{machine} ear (agentic_irc) owns !bored -> OFFER in #{machine}
Workers ACK/DONE in their own #{machine} only
```

Trust: no HMAC on fleet GIT hooks; digest writes use a shared-secret header stored outside git. Jeeves never edits Ergo/BobIrcd. Secrets never in git, issues, or channel.

LOCKED

## Screens

Jeeves has no product UI. Mocks represent the `!list` / health text view for the vision-pack validator.

| id | file | state |
|----|------|-------|
| M1 | docs/mocks/home.html | primary (!list with unaccepted rows) |
| M2 | docs/mocks/empty.html | empty queue |
| M3 | docs/mocks/error.html | health / announce failure |

## LOCKED

- Objective: deterministic GIT chair; token-less path is the acceptance gate
- Shape: service
- Stack: Python + PowerShell / NSSM on Windows; Ergo TLS IRC; public GitHub
- S1 = brief section 0 G1/G2 gate
- CAST IRON: announce only on #bobiverse; silent shop listener; never !bored/offers; supersede table; own Windows service
- Scope: announce, queue+supersede, shop ACK/DONE, !list, webhook writer, GIT receiver, service installer, G1 tests, skills overlay
- Non-goals: LLM features, offering/assigning, Ergo config, TipForm UI, worker implementation
- Target repo: SimonBarnett/gh-Jeeves (public)

## UNKNOWN

- Who emits ASSIGN today (proposal: ear OFFER grammar only)
- Exact IIS rewrite to loopback receiver
- Why BobIrcd shows Stopped while ergo.exe runs (report only; do not touch)
- Exact Ergo throttle values and default seat_recv_idle_s
- Full drain-report document location for 2026-09-25 dry run
