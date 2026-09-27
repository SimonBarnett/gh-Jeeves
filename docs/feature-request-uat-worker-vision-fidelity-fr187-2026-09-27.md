# FR #187: UAT as worker-assignable vision-fidelity task

## Objective

After MRB PASS merges to **main**, Jeeves assigns **UAT** to a worker the same
way it assigns FR/MRB. The UAT worker checks that current `main` matches the
repo vision pack and functional spec. UAT is **not** a human-only Bob stamp.

LOCKED (Simon 2026-09-27 on issue #187).

## Success

| id | metric | target | how measured | fail-when |
|----|--------|--------|--------------|-----------|
| S1 | UAT is first-class shop task | Assign/ACK/DONE accept `UAT`; queue enqueues UAT after MRB PASS merge | existing wire + queue tests; skills say worker UAT | Docs/skills still say only Bob stamps UAT |
| S2 | Vision-fidelity checklist | `skills/jeeves-uat` lists vision → poke → tests → PASS/FAIL | skill present + inventory test | Skill missing or Bob-only |
| S3 | DONE wire unchanged | `DONE UAT owner/repo#n PASS\|FAIL <url>` only; notes on separate lines | `parse_done` + skill | Extra tokens jammed on DONE line |
| S4 | Token-less path untouched | G1/G2 and supersede scripts unchanged in behaviour | G1 still green; no LLM on chair path | UAT skill required on token-less path |

## Shape / stack

Unchanged: gh-Jeeves **service** + skills overlay. UAT worker packs live in
fleet skill books (`agentic_build` follow-up); this repo owns chair grammar,
queue supersede, and the `jeeves-uat` playbook.

## Locked answers (open questions on #187)

| Question | Lock |
|----------|------|
| Who stamps UAT PASS/FAIL? | **UAT worker** via shop `DONE UAT … PASS\|FAIL <url>` |
| Does UAT PASS close the source FR? | Source FR already closed on MRB PASS merge; UAT DONE completes the **UAT** queue row |
| G1/G2 relationship? | **Orthogonal** — UAT is agentic overlay; non-goal to change token-less path |
| FAIL filing? | Worker opens **issues** (broken) / **FRs** (missing); FRs follow MRB #1 (`needs-mrb1`) |
| Extra DONE fields? | **None** on the DONE line; tests added / issues opened on **separate** outbox lines |

## Out of scope (this PR)

Sister-repo skill/README updates (`agentic_build`, `agentic_irc`,
`AgentMonitor`, `skills-visionary`, `bob-design-uat`) — park as follow-up FRs.
`design-uat` (visual mocks) stays distinct from vision-fidelity UAT.

## Non-goals

- Changing MRB #1 / MRB #2 gates.
- Changing the token-less G1/G2 path.
