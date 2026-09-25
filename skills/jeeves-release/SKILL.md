---
name: jeeves-release
description: >
  Tag a gh-Jeeves release, deploy from the tag on ionos, and roll back to the
  previous release or legacy BobJeeves-chair task. Use when shipping Jeeves,
  version drift, or /jeeves-release.
---

# jeeves-release

## Purpose

**K5 / FR #6:** ship Jeeves from a **tagged release**, not a detached hotpatch
tree (`C:\ai\agentic_irc-www@…`). Running version is reported to the digest
webhook; health compares it to the expected tag (**drift check**).

Agentic control is an overlay: the token-less path must never depend on this skill.

## Version identity

| Source | Role |
|--------|------|
| `VERSION` file in release tree | Canonical package version (e.g. `0.2.0`) |
| `EXPECTED_RELEASE` | Pin expected tag (`v0.2.0`) on the box |
| `JEEVES_RELEASE_TAG` env | Service override |
| `python -c "from jeeves.versioning import running_version; print(running_version())"` | Process identity |

## Tag a release (maintainer)

```powershell
# from main, clean tree
git tag -a v0.2.0 -m "gh-Jeeves v0.2.0"
git push origin v0.2.0
# ensure VERSION file matches (no leading v)
```

## Deploy from tag (operator; not FR workers)

```powershell
# Plan only (CI-safe)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Deploy-BobJeevesRelease.ps1 -Tag v0.2.0 -DryRun -Json

# Apply clone into ~/jeeves-releases/v0.2.0 (elevated box you administer)
# powershell -File tools\Deploy-BobJeevesRelease.ps1 -Tag v0.2.0 -Apply

# Point BobJeeves at that tree; set JEEVES_RELEASE_TAG=v0.2.0
# tools\Install-BobJeeves.ps1 -DryRun -Json
```

**Never** edit Ergo / BobIrcd from this skill.

## Drift check

```powershell
python -c "from jeeves.versioning import check_drift; import json; print(json.dumps(check_drift().as_dict(), indent=2))"
```

- `drifted=false` / `reason=match` → healthy
- `tag_mismatch` / `commits_after_tag` / `dirty_tree` → redeploy from tag

## Webhook report

`version_report_payload()` embeds:

```json
{ "version": "v0.2.0", "jeeves_version": {...}, "version_drift": {...} }
```

Receiver GET `/bob/v1/report` merges this when the chair stamps `jeeves_version.json`
under the digest home at start.

## Rollback

1. `Deploy-BobJeevesRelease.ps1 -Tag vPREV -Apply`
2. Repoint service AppDirectory / env to previous release dir
3. Or temporarily re-enable task `BobJeeves-chair` only as last resort (document in notes)

## Tests

```text
pytest -q tests/test_version_drift_fr6.py
```

## Related

- FR #6 K5, agentic_build #330, `jeeves-install-service`, `jeeves-health`
