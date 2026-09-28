# FR #211: !recycle {machine} vs bare all

## Behaviour
- `!recycle` / `!recycle all` → `RECYCLE machine=fleet scope=fleet` on #bobiverse (every bob-*)
- `!recycle {machinename}` → `RECYCLE machine=<id> scope=local` on command channel + #{machine}
- Unknown machine → denied with fleet list
- Aliases: dev1 → ce-priority-dev1

## Split
Jeeves routes only; bob-* announce restarting then execute (agentic_irc #249).