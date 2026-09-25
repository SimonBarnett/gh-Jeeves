# FR #92: no-self-merge + real MRB (enforcement)

## Problem

Fleet seats share one GitHub identity (`SimonBarnett`). Prompt-only rules
("never merge your own PR", "hostile MRB must run full tests") were broken:

- seats merged their own FAIL-fix PRs
- PASS arrived in under two minutes with scoped tests only
- seats ACKed MRB on their own PRs

## Design (option 2 — status check)

Required check name: **`mrb/verdict`**

| Field | Source |
|-------|--------|
| Author seat | PR body line `Seat: {nick}` (set when opening the FR PR) |
| Reviewer seat | Seat posting the verdict (`Post-MrbVerdict` / `post_mrb_verdict.py`) |
| Full suite | `pytest_cmd` must match a full `tests/` run; `pytest_exit` must be 0 for PASS |
| Duration | Wall clock ≥ 10 minutes (600 s) by default |

Rules implemented in `jeeves.mrb_gate`:

1. **reviewer ≠ author** (case-insensitive nick)
2. **PASS ⇒ full suite exit 0**
3. **duration ≥ min** (default 600 s)
4. FAIL with a valid process posts check conclusion **`neutral`** (recorded, not a green merge)
5. Validation errors post conclusion **`failure`**

Simon retains admin override on branch protection.

## Seat workflow

### Author (FR worker)

1. Open PR with body containing:
   ```text
   Seat: flamingo-43052
   ```
2. Do **not** merge. Do **not** post `mrb/verdict` on your own PR.

### Reviewer (MRB worker)

1. Hostile review; run **full** suite and keep wall time:
   ```text
   python -m pytest tests/ -q
   ```
2. Post verdict (example PASS after 12+ minutes, exit 0):
   ```powershell
   python tools/post_mrb_verdict.py --repo SimonBarnett/gh-Jeeves --pr N `
     --reviewer-seat ionos-9504 `
     --pytest-cmd "python -m pytest tests/ -q" --pytest-exit 0 `
     --duration-s 720 --verdict PASS
   ```
3. On PASS + green `mrb/verdict`, merge (if branch protection allows).
4. On FAIL, open one fix PR; do not self-merge the fix if you authored it — another seat MRBs.

### Simon

1. Settings → Branches → protect `main`:
   - Require status checks: **`mrb/verdict`**, **`Full test suite / tests`** (and G1 as desired)
   - Require branches to be up to date (optional)
   - Restrict who can push (optional)
   - Allow administrator bypass for Simon only
2. Do not grant seats admin on the repo.

## Why not GitHub "approving reviews"?

Same human account authors and "reviews" → GitHub cannot distinguish seats.
Per-seat GitHub Apps (option 1 in the FR) remain a future upgrade; this check
works with the shared account **if** seats tell the truth in `Seat:` + reviewer
args. Lying seats are a social/ops problem; the check still blocks short/scoped
PASS theatre and same-nick self-MRB when trailers are honest.

## Reuse

Same pattern for `agentic_irc`, `agentic_build`, `AgentMonitor` (follow-up FRs):
copy `mrb_gate.py` + `post_mrb_verdict.py` + branch protection docs.

## Related

- FR #92, #96 (full suite CI)
- skills: `jeeves-task-modes`
