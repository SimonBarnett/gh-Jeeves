---
name: jeeves-uat
description: >
  Vision-fidelity UAT after MRB PASS: Jeeves assigns UAT like FR/MRB; worker
  checks main against docs/vision.md + functional spec, pokes code, adds
  tests if needed, runs full suite, DONE UAT PASS|FAIL. Not Bob-only. Not
  design-uat (visual mocks). Use when assigned UAT, /jeeves-uat, or docs
  still say only Bob stamps UAT.
github: https://github.com/SimonBarnett/gh-Jeeves
---

# jeeves-uat (vision-fidelity UAT)

FR #187. Agentic control is an **overlay**: the token-less G1/G2 path must
**never** depend on this skill or an LLM.

## What changed

UAT is a **worker-assignable** shop task (`FR` / `MRB` / `UAT`). After MRB
PASS merges to `main`, Jeeves enqueues **UAT** and assigns it on `!bored`.
The **UAT worker** stamps PASS/FAIL via `DONE` — not a human-only Bob stamp.

`design-uat` (visual check against `docs/mocks/` + briefs) is a **different**
product. This skill is **vision-fidelity of current main**.

## Wire (CAST IRON)

```text
ACK UAT <owner/repo>#<n>
DONE UAT <owner/repo>#<n> PASS|FAIL <url>
```

- Keyword first; no nick prefix; nothing after the URL on the DONE line.
- Notes (tests added, issues opened) on **separate** outbox lines.
- Then `!bored` (monitor preferred).

`<url>` is usually the merged PR that produced the UAT row, or the vision
doc / board URL when that is the evidence link.

## Checklist (on current `main`)

1. Read `docs/vision.md` (and visionary brief if present) + `docs/functional-spec.md`.
2. Confirm the project direction in vision is reflected in the tree.
3. Confirm assets/behaviours named in the customer/spec docs actually exist.
4. **Poke the code thoroughly before adding tests.**
5. Add any missing tests that the poke revealed; run them.
6. Run the **full** suite (`python -m pytest tests/` or repo equivalent).
7. **PASS** → `DONE UAT … PASS <url>`.
8. **FAIL** → open **issues** for broken behaviour and/or **FRs** for missing
   pieces (FRs get `needs-mrb1` / MRB #1 as usual); then
   `DONE UAT … FAIL <url>` with links on separate lines.

## Who may stamp

| Role | May DONE UAT? |
|------|----------------|
| UAT worker (separate seat from MRB when 2+ free) | **Yes** |
| MRB worker on the same merge | **No** — never stamp UAT from the MRB seat |
| Bob | May observe / override; **not** required for routine PASS |

## Queue

Supersede unchanged: MRB PASS merged → enqueue UAT; UAT DONE → done + idle.
See `jeeves-queue`, `jeeves-task-modes`.

## Do not

- Require Bob to stamp routine UAT PASS.
- Put LLM calls on the Jeeves chair / G1 path.
- Jam issue lists onto the DONE line.
- Conflate this with `design-uat` visual review.
