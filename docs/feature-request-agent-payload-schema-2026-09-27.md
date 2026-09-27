# FR: Agent payload schema for intake / harvest emissions

**Date:** 2026-09-27
**Repo:** SimonBarnett/gh-Jeeves
**Status:** proposal — no code changes in this PR

## Current state

The harvest skill (`skills/harvest/SKILL.md`) and `src/jeeves/intake.py` (FR #26) already define the intake payload fields informally:

- `kind` (`issue` | `fr` | `skill` | `harvest`)
- `repo`
- `title`
- `body`
- optional `files[]` (`path` + `content`, small)
- `source` (machine, agent/tool, skill book + version)
- optional `contact`, `contact_public`
- `idempotency_key`
- optional fleet intake key in header `X-Bob-Intake-Key`

Validation lives in `validate_payload()` and is enforced server-side. Agents currently construct payloads ad hoc from the skill text, which drifts and produces 4xx rejects.

## Proposal

Ship a canonical **JSON Schema** (draft 2020-12) that agents emit against before POSTing to `/bob/v1/intake` or opening a PR. One schema, two consumers: the harvest skill text and any future agent tooling.

### Required fields

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | string enum | `issue`, `fr`, `skill`, `harvest` |
| `repo` | string | `owner/name`, must be in allow-list |
| `title` | string | 1..200 chars |
| `body` | string | |

### Optional fields

| Field | Type | Notes |
| --- | --- | --- |
| `files` | array of `{path, content}` | max 32 files, 128 KiB each, 256 KiB total |
| `source` | object | `machine`, `agent`, `skill_book`, `version` |
| `contact` | string | max 200 chars; never logged in clear |
| `contact_public` | boolean | |
| `idempotency_key` | string | max 128 chars; retries dedupe |

### Routing rule (unchanged)

- `kind` in (`issue`, `fr`) → issue (label `feature-request` for `fr`, plus `needs-mrb1`)
- `kind` in (`skill`, `harvest`) → draft PR on branch `intake/<id>`, fallback to issue with file listing if PR creation fails

### Acceptance criteria

1. Schema file committed under `docs/` (e.g. `docs/agent-payload.schema.json`).
2. `skills/harvest/SKILL.md` references the schema path instead of inlining field lists.
3. `validate_payload()` either generates from the schema or stays in sync via a test that loads both.
4. A unit test rejects each known 4xx class (`bad_kind`, `bad_repo`, `bad_title`, `payload_too_large`, `empty_harvest`, ...) with a fixture per case.
5. No secrets, machine IDs or personal paths in schema examples — placeholders only.

## Why now

Intake is live and agents are filing through it. A shared schema kills the drift between skill prose and server validation, and gives offline `harvest-outbox/` writers something to validate against before retry.

## Out of scope

- Changing the webhook path, headers, or allow-list.
- Token-less G1 path (#1) — agentic control stays an overlay.
