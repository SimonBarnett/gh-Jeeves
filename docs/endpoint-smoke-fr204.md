# Endpoint smoke scripts (FR #204)

Canonical base URL: **`https://bob.ntsa.uk`**

## Scripts (`tools/`)

| Script | Group |
|--------|--------|
| `test_git_endpoints.py` | `POST /bob/v1/git` |
| `test_jira_endpoints.py` | `POST`/`GET /bob/v1/jira` |
| `test_report_endpoints.py` | `POST`/`GET` report + digest aliases |
| `test_intake_endpoints.py` | `POST`/`GET` intake |

Shared helpers: `tools/smoke_common.py` (stdlib only).

## Local / StubReceiver

```powershell
$env:PYTHONPATH = 'src'
$env:BOB_SECRET = 'dev-secret'
# start StubReceiver in tests, or:
python tools/test_git_endpoints.py --base-url http://127.0.0.1:8787 --json
```

Env override: `BOB_SMOKE_BASE_URL` (or `--base-url`).

## Against production host

```powershell
$env:BOB_SECRET = '<from secret store>'   # never commit / never log
python tools/test_report_endpoints.py --json
```

Opt-in pytest live job:

```powershell
$env:BOB_SMOKE_LIVE = '1'
$env:BOB_SECRET = '<ci secret>'
pytest tests/test_endpoint_smoke_fr204.py -k live -q
```

Without `BOB_SECRET`, jira/intake report a clear credential failure/skip — they do not silently pass writes.

## Safety

- Git: ping + malformed cases; no real worker ACK.
- Report: `external:true` unique ids only (never fleet `machines.*`).
- Jira: `FR204-SMOKE-*` keys.
- Intake: synthetic idempotency keys `fr204-*`.
- Scripts redact secret env values from printed details.
