# FR #159: per-machine webhook/digest updates for fleet seats

## Problem

Ear/tray `op=merge` heartbeats could:

1. Report `machine: bob-marchhare` / `dev1` and land beside the canonical fleet key
2. Replace `machines.<id>.workers` with `{}` and wipe ACK/DONE seat state
3. Blank `working_on` while seats were still busy

## Fix

- `MACHINE_ID_ALIASES` + `normalize_machine_id` map `bob-*` / `dev1` → canonical ids
- Merge worker dicts (never replace-with-empty)
- After ear merge, re-project top-level queue workers into `machines.*.workers` and refresh `working_on`

## Tests

`tests/test_machine_digest_fr159.py` — all four of `flamingo`, `marchhare`, `ionos`, `ce-priority-dev1`

Cross-link: closed #79 (empty workers view).
