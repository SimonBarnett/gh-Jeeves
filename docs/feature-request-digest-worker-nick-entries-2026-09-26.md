# FR #162: digest one entry per present worker (nick-keyed, by machine)

## Required shape

- Exactly **one** digest entry per present worker, keyed by **worker nick**
- Grouped under `machines.<machinename>.workers`
- Not one entry per machine only
- No stale **pid ghost** twins (`"31712"` beside `"marchhare-31712"`)

## Fix

- `mirror_top_worker_to_machine` writes nick keys only
- `coerce_workers` / `nick_keyed_machine_workers` / `prune_machine_worker_ghosts` promote ear pid rows to nick and drop digit-only ghosts
- `public_digest_snapshot` prunes on every GET

## Tests

`tests/test_digest_workers_fr162.py` (+ TipForm FR #13 asserts updated for nick-only)

Cross-link: closed #79 (empty `machines.*.workers` while top tracked busy).
