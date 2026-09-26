# FR #160: auto-login / auto-oper Simon without Ergo config edits

## Requirement

When nick/account `simon` connects from Simon's fleet machines, Jeeves should
get him logged into account `simon` and opered **without** Simon typing
`/msg NickServ` or `/oper`, and **without** editing `C:\ai\ergo` / `ircd.yaml`.

## Runtime investigation (Ergo integrated NickServ)

Upstream Ergo NickServ admin / SA* surface (see `irc/nickserv.go`):

| Verb | Effect |
|------|--------|
| `SAREGISTER` / `SAVERIFY` / `SADROP` | Account lifecycle |
| `SAGET` / `SASET` | Read/write another account's settings |
| `CERT ADD/DEL` (with `accreg`) | Manage certfp on an account |
| `PASSWD <user> <new>` (oper) | Reset password |
| `SUSPEND` | Ban account |

**Missing:** `SAIDENTIFY`, `FORCELOGIN`, `SALOGIN`, or any command that attaches
account `simon` to an already-connected session that did not present SASL /
PASS / certfp itself.

**OPER:** Ergo grants operator status from the `opers:` block in `ircd.yaml`
(hashed password or certfp). There is no IRC SA* that opers a third party at
runtime. Changing that block is an Ergo config edit — forbidden by this FR.

## Conclusion (no_runtime_path)

Ergo offers **no** token-less runtime path for Jeeves to auto-IDENTIFY and
auto-OPER Simon's Halloy session. Halloy's
`Nickname is reserved by a different account` loop is fixed by **client**
SASL (or `PASS simon:<password>`, or CERTFP registered on the account) —
not by Jeeves inventing a password or editing Ergo.

## What Jeeves does instead

1. **FR #52 (unchanged):** authenticated `simon` (account `simon`) from a fleet
   host receives channel `+o`.
2. **FR #160:** if nick `simon` appears from a fleet host **without** a services
   account, Jeeves records `no_runtime_path` once and PMs a deterministic hint
   (SASL / PASS / CERTFP; OPER needs ircd.yaml). No secrets stored.

## Tests

`tests/test_simon_auto_auth_fr160.py`
