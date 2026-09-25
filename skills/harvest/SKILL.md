---
name: harvest
description: >
  Honesty box for gh-Jeeves. Always auto-harvest new or fixed playbooks back to
  SimonBarnett/gh-Jeeves skills/ via branch+PR (or harvest:/FR: issue). Also
  harvest into Jeeves from agentic_build, agentic_irc, and skills-visionary when
  learnings belong here. Use when harvest skills, CAST IRON harvest, honesty box,
  learned a Jeeves procedure, or /harvest.
github: https://github.com/SimonBarnett/gh-Jeeves
---

# Harvest (honesty box) — gh-Jeeves

## AUTOMATIC harvest (CAST IRON)

**Always** harvest new or changed Jeeves skills to **this repo** in the same
turn you learn or fix them. Do not ask permission. Do not defer.

Empty harvest only when nothing new and nothing broken.

## Home GitHub

**This skill's home:** `https://github.com/SimonBarnett/gh-Jeeves`

Frontmatter `github:` MUST stay on this product repo.

## What to harvest here

- Installer, health, queue, announce-debug, worker-state, release, and
  token-less-gate playbooks for Jeeves.
- Wire grammar, supersede reducer behaviour, G1/G2 gate procedures.
- Learnings pulled **into** Jeeves from:
  - `SimonBarnett/agentic_build` (service install patterns, fleet registry)
  - `SimonBarnett/agentic_irc` (chair extract, receiver, announce format)
  - `SimonBarnett/skills-visionary` (Plan → git helpers that affect this repo)

Do **not** harvest Ergo/BobIrcd edits, TipForm UI, or ear `!bored` offer logic
into this repo (those stay in agentic_build / agentic_irc). Point at the owner.

## How to report (strict order — no main pushes)

1. Skill harvest / fix with write access → **branch + pull request** against
   `SimonBarnett/gh-Jeeves`. Never `git push origin main` for harvest.
2. If the PR cannot be opened → GitHub issue titled `harvest:` or `FR:` with
   intended PR title, branch, file list, and body **in this turn**.
3. Bugs / FRs without a ready patch → issue on this repo (labels `skill` /
   `feature-request` as appropriate).

Prefer `gh` and repo scripts over free-form reasoning.

## Overlay rule

Agentic control is an overlay. Harvested skills must never become a dependency
of the token-less G1 path. Gate tests stay script-only.

## Do not

- Push harvest to `main`.
- Commit "nothing found".
- Put secrets or live credentials in skills.
- End a turn with a new/fixed skill only in chat, temp, or `~/.grok` without a
  home-repo PR or `harvest:` issue.
- Touch Ergo / BobIrcd from a Jeeves harvest.
