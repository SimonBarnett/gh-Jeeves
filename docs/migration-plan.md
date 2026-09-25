# Migration plan (from brief section 16)

Jeeves moves out of `agentic_irc` into **SimonBarnett/gh-Jeeves** with its own service, tests, and release cycle.

## Phase 0: freeze and document

- Tag the current chair code in agentic_irc (`jeeves-legacy`).
- Record ionos drift: Jeeves and the receiver run from `C:\ai\agentic_irc-www@ac4b575`, bob-ionos from a stale branch, and BobIrcd is Stopped while ergo.exe runs.

## Phase 1: extract

- Copy `gitclaim.py`, the chair parts of `irc_agent.py`, and the announce/webhook parts of `bobreport.py` / `bobcallback.py` into gh-Jeeves, with history if practical (`git filter-repo`).
- Keep the wire format byte-compatible. Add the local-ircd test harness and G1.

## Phase 2: rule fixes in gh-Jeeves

- remove `!BORED` handling from the chair;
- add the ACK/DONE listener (#211), the supersede reducer and resync (#207), `!list` (#208), the secret filter (#206), line splitting (#205), and webhook busy/idle.

## Phase 3: ear takes the claim

In agentic_irc, the bob-{machine} ear implements `!bored` → offer (one at a time, skipping busy workers) → reads the ACK. Worker packs send `!bored`, `ACK` and `DONE` in the grammar. Ship this in the **same release window** as phase 2, so there is never a period with no claimer.

## Phase 4: service

- gh-Jeeves installs `BobJeeves` (#330), disables `BobJeeves-chair` and deploys from a release tag.
- `Install-BobFleet.ps1` on ionos calls it.
- One-off: resync the live queue from GitHub (clearing the 28 stale rows).

## Phase 5: cut-over and clean-up

- Remove the chair code from agentic_irc, leaving a thin shim that errors "moved to gh-Jeeves".
- Update the skills (bob-jeeves-chair, bob-git-accept, bob-token-handoff) and the agentic_build README diagrams 2–4 so they say **Jeeves assigns** (FR #11 / K10; gh-Jeeves README is SoT).
- Fix the duplicate `reportUrl` key.

## Phase 6: ops

After Simon approves, agentic_build #327 enables registration and ops.

## Rollback

Re-enable task `BobJeeves-chair` on the tagged legacy checkout. The queue format stays compatible.

## 16.3 Live smoke test procedure (G2)

1. Pre-check:
   - `GET https://irc.ntsa.uk/bob/v1/report`: Jeeves `lastSeen` is fresh.
   - The ear on the test machine and one worker seat are configured with **no token pools**, and the worker is a scripted fake that replies ACK and DONE.
2. On a sandbox repo with the Bob hook, open issue "smoke &lt;timestamp&gt;". Expect `GIT issues &lt;repo&gt; opened #n` on #bobiverse (ionos `~\.agentic-irc-bobiverse\irc.log`) and `FR #n` in `queue.unaccepted`.
3. The worker sends `!bored` in `#{machine}`. Expect the ear's `OFFER FR &lt;repo&gt;#n` addressed to that nick.
4. The worker sends `ACK FR &lt;repo&gt;#n`. Expect the row in `queue.accepted` and the worker busy in `machines.&lt;id&gt;.workers`.
5. Open a PR with `Closes #n`. Expect `FR #n` to be replaced by `MRB #pr`.
6. The worker sends `DONE FR &lt;repo&gt;#n PR &lt;url&gt;`. Expect the worker idle and the row done.
7. Close the PR unmerged. Expect `FR #n` restored. Close the issue. Expect it removed.
8. Record the timings and the log excerpts in the gh-Jeeves release notes.
