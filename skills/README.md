# gh-Jeeves skills

Agent-controllable surface for operating, diagnosing, and changing Jeeves.

**Agentic control is an overlay.** The section 0 token-less path (GitHub → announce → queue → !bored → **Jeeves assign** → ACK → DONE → supersede) must **never** depend on a skill or an LLM. Skills help humans and agents; scripts own the gate (#1 / FR #106).

| Skill | Purpose | Seed / refs |
|-------|---------|-------------|
| [harvest](harvest/SKILL.md) | Honesty box: report back via `gh` PR/issue → `/bob/v1/intake` → `harvest-outbox/` | #26 |
| [jeeves-install-service](jeeves-install-service/SKILL.md) | Install / repair / upgrade `BobJeeves` (never Ergo) | #17 |
| [jeeves-health](jeeves-health/SKILL.md) | Service, IRC presence, lastSeen, version drift, throttle, ops | #18 |
| [jeeves-queue](jeeves-queue/SKILL.md) | Queue, supersede rules, `!list`, `!help`, resync; dry-run first | #19, #25, #27 |
| [jeeves-announce-debug](jeeves-announce-debug/SKILL.md) | Hook delivery not announced; 417 / length-safe lines | #20, #24 |
| [jeeves-worker-state](jeeves-worker-state/SKILL.md) | ACK/DONE → accepted/busy/activity, done/idle; stuck accepted | #21 |
| [jeeves-release](jeeves-release/SKILL.md) | Tag, deploy, G2, roll back | #22 |
| [jeeves-token-less-gate](jeeves-token-less-gate/SKILL.md) | Run G1 local E2E and G2 live smoke | #23, #1 |
| [jeeves-shop-protocol](jeeves-shop-protocol/SKILL.md) | `#{machine}` wire: `!bored` → Jeeves assign → ACK → DONE | #1, #106, #146 |
| [jeeves-task-modes](jeeves-task-modes/SKILL.md) | FR / MRB / UAT contract; MRB tests-first, docs PR on PASS, fix PR on FAIL | #1 |
| [jeeves-mrb-gates](jeeves-mrb-gates/SKILL.md) | MRB #1 vision fit (issues) + MRB #2 implementation (PRs) | #151, #92 |
| [jeeves-irc-roles](jeeves-irc-roles/SKILL.md) | Nicks, channels, secrets and token-free listening | — |

Foundation twin: `.grok/skills/harvest-agent-skills` → `skills/harvest`.
