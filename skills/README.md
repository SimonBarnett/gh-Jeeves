# gh-Jeeves skills

Agent-controllable surface for operating, diagnosing, and changing Jeeves.

**Agentic control is an overlay.** The section 0 token-less path (GitHub → announce → queue → !bored → offer → ACK → DONE → supersede) must **never** depend on a skill or an LLM. Skills help humans and agents; scripts own the gate.

| Skill | Purpose | Seed / refs |
|-------|---------|-------------|
| [harvest](harvest/SKILL.md) | Honesty box: PR learnings; intake when no `gh` | #26 |
| [jeeves-install-service](jeeves-install-service/SKILL.md) | Install / repair / upgrade `BobJeeves` (never Ergo) | #17, #48 |
| [jeeves-health](jeeves-health/SKILL.md) | Service, IRC presence, lastSeen, version drift | #18 |
| [jeeves-queue](jeeves-queue/SKILL.md) | Queue / resync / !list / !ignore | #19, #25, #27, #75 |
| [jeeves-announce-debug](jeeves-announce-debug/SKILL.md) | Hook delivery not announced | #20, #24 |
| [jeeves-worker-state](jeeves-worker-state/SKILL.md) | ACK/DONE, busy/idle, stuck accepted | #21 |
| [jeeves-release](jeeves-release/SKILL.md) | Tag, deploy, roll back | #22 |
| [jeeves-token-less-gate](jeeves-token-less-gate/SKILL.md) | G1 local E2E and G2 live smoke | #23, #1 |
| [jeeves-shop-protocol](jeeves-shop-protocol/SKILL.md) | Shop claim wire: !bored → ear offer → ACK/DONE | CAST IRON |
| [jeeves-task-modes](jeeves-task-modes/SKILL.md) | FR / MRB / UAT contracts and DONE grammar | #1 |
| [jeeves-irc-roles](jeeves-irc-roles/SKILL.md) | Nick + channel matrix; secrets out of git | CAST IRON |

Foundation twin: `.grok/skills/harvest-agent-skills` → `skills/harvest`.

## MRB note (PR #28)

Original harvest PR #28 conflicted with main after FR #48/#71/#74/#75 skill edits. Survivors from that harvest are the three shop/roles/modes skills above; install/queue/harvest on main already superseded the older harvest body.
