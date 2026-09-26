# FR #161: ACK/DONE drive deterministic worker busy-idle digest state

## Shape

On **ACK**, each worker digest entry (top-level and `machines.<id>.workers[nick]`) carries:

| Field | Meaning |
|-------|---------|
| `state` | `busy` |
| `repo` | owner/name |
| `kind` | `FR` / `MRB` / `UAT` |
| `id` | `#n` |
| `ref` | `repo#n` |
| `title` | issue/PR title (from queue `line`/`title`) |
| `job` | legacy string `repo KIND #n` |

On **DONE**, the same entry returns to **idle** with task fields cleared (`job`/`repo`/`kind`/`id`/`ref`/`title` null). Idle holds until the next ACK — GET heal must not resurrect the prior task.

## Tests

`tests/test_ack_done_busy_idle_fr161.py`
