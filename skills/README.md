# gh-Jeeves skills

Agent-controllable surface for operating, diagnosing, and changing Jeeves.

**Agentic control is an overlay.** The section 0 token-less path (GitHub → announce → queue → !bored → offer → ACK → DONE → supersede) must **never** depend on a skill or an LLM. Skills help humans and agents; scripts own the gate.

| Skill | Purpose |
|-------|---------|
| [harvest](harvest/SKILL.md) | Honesty box: always PR learnings back to this repo; also harvest into Jeeves from fleet books |
| [jeeves-install-service](jeeves-install-service/SKILL.md) | Install / repair / upgrade `BobJeeves` (never Ergo) |
| [jeeves-health](jeeves-health/SKILL.md) | Service, IRC presence, lastSeen, version drift, throttle |
| [jeeves-queue](jeeves-queue/SKILL.md) | Read / explain / resync / repair queue; dry-run first |
| [jeeves-announce-debug](jeeves-announce-debug/SKILL.md) | Hook delivery not announced |
| [jeeves-worker-state](jeeves-worker-state/SKILL.md) | ACK/DONE, busy/idle, stuck accepted |
| [jeeves-release](jeeves-release/SKILL.md) | Tag, deploy, roll back |
| [jeeves-token-less-gate](jeeves-token-less-gate/SKILL.md) | Run G1 local E2E and G2 live smoke |

Foundation twin: `.grok/skills/harvest-agent-skills` → `skills/harvest`.
