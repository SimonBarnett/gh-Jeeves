# FR #148: deterministic exception → GitHub issue (no LLM)

Any uncaught exception in `python -m jeeves` is reported by scripts only:
template, dedupe, rate limit, local spool + drain. No LLM.

## Entry

- `python -m jeeves` → `_main_with_exception_report` (catch-all + `sys.excepthook`)
- CLI: `python tools/file_exception_issue.py --test-raise` / `--drain`
- Module: `python -m jeeves.exception_report`

## Label (must exist)

Issues are filed with label `auto-exception`. Create once:

```text
python tools/ensure_auto_exception_label.py --repo SimonBarnett/gh-Jeeves
```

Missing label → GitHub HTTP 422 → record lands in `exception_spool/` until fixed.

## Auth

Token resolution order: `JEEVES_EXCEPTION_GITHUB_TOKEN`, `GITHUB_TOKEN`,
`GH_TOKEN`, then `github.token` under Jeeves / digest home. No token → spool
with reason `no_token` (token-less chair keeps running).

## Related

- FR #148, `src/jeeves/exception_report.py`, `tests/test_exception_report_fr148.py`
