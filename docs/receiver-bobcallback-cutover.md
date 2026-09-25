# Receiver cutover: bobcallback → gh-Jeeves (FR #47)

Local machine / ionos only. **Never** edit Ergo or restart unrelated services from this doc.

## Contract (drop-in)

| Item | Behaviour |
|------|-----------|
| Listen | Configurable; ionos default **127.0.0.1:19781** (IIS reverse-proxy) |
| GET `/bob/v1/report`, `/bob/v1/digest`, `/digest` | Public digest JSON (machines, cursor_pools, pcent, lastSeen, queue) |
| POST `/bob/v1/report` | Requires **`X-Bob-Secret`** → 401 if missing/wrong (when secret configured / `bob.secret` present) |
| POST `/bob/v1/git` | **No** `X-Bob-Secret` (fleet hooks carry no secret; vision Trust / BRIEF). Secret-field filter (K14) only |
| POST `/bob/v1/intake` | Requires **`X-Bob-Secret`** when secret configured |
| Secret source | `BOB_CALLBACK_SECRET` / `BOB_SECRET` env, or `bob.secret` file under digest home — **never logged** |

## Cutover (ionos)

1. Stop old `bobcallback` / BobReport process listening on 19781.
2. Ensure digest home is the same path ears already use (`BOB_DIGEST_HOME`, typically `~/.agentic-irc-bobiverse`).
3. Place shared secret where gh-Jeeves loads it (`BOB_CALLBACK_SECRET` or `%BOB_DIGEST_HOME%\bob.secret`).
4. Start `python -m jeeves receiver` (or combined `python -m jeeves all`) with `--receiver-port 19781`.
5. Confirm:
   - `GET http://127.0.0.1:19781/bob/v1/report` returns `machines` + `cursor_pools`.
   - `POST` without header → **401**.
   - Ear/tray `op=merge` with secret updates `machines.<id>.lastSeen` / `pcent`.
6. Leave IIS binding unchanged.

## Rollback

1. Stop gh-Jeeves receiver.
2. Start previous bobcallback on 19781 with the same secret and digest home.
3. Digest file `digest.json` is compatible enough for trays; if needed restore from backup taken before cutover.

## Tests

```text
pytest -q tests/test_bobcallback_dropin_fr47.py
```
