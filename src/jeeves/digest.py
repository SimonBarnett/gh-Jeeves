"""Fleet digest + report merge (FR #47 bobcallback drop-in shape).

GET digest must stay tray/TipForm compatible: machines.*, cursor_pools, pcent,
lastSeen, queue/workers. POST /bob/v1/report accepts ear/machine merge ops.
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FLEET_MACHINE_IDS = (
    "flamingo",
    "marchhare",
    "ionos",
    "ce-priority-dev1",
)

_SECRET_MARKERS = (
    "BEGIN PRIVATE KEY",
    "ghp_",
    "gho_",
    "github_pat_",
    "xoxb-",
    "sk-or-",
    "sk-ant-",
)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def digest_path(home: Path) -> Path:
    return Path(home) / "digest.json"


def normalize_machine_id(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if s in FLEET_MACHINE_IDS:
        return s
    # allow unknown machines through (normalized slug)
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-")
    return s


def shop_channel(mid: str) -> str:
    return f"#{normalize_machine_id(mid) or mid}"


def empty_machine(mid: str) -> dict[str, Any]:
    m = normalize_machine_id(mid) or mid
    return {
        "id": m,
        "nick": f"bob-{m}",
        "shop": shop_channel(m),
        "online": False,
        "status": "I am offline",
        "working_on": "",
        "workers": {},
        "lastSeen": "",
        "pcent": {},
        "running": 0,
        "queued": 0,
    }


def empty_digest() -> dict[str, Any]:
    return {
        "v": 1,
        "ts": "",
        "briefer": "",
        "machines": {mid: empty_machine(mid) for mid in FLEET_MACHINE_IDS},
        "cursor_pools": [],
        "events": [],
        "queue": {"unaccepted": [], "accepted": [], "done": [], "workers": {}},
    }


def looks_like_secret(text: str) -> bool:
    if not text:
        return False
    for m in _SECRET_MARKERS:
        if m in text:
            return True
    return False


def load_digest(home: Path) -> dict[str, Any]:
    path = digest_path(home)
    if not path.is_file():
        return empty_digest()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_digest()
    if not isinstance(doc, dict):
        return empty_digest()
    return ensure_seats(doc)


def save_digest(home: Path, doc: dict[str, Any], *, retries: int = 5, backoff_s: float = 0.05) -> None:
    """Atomic write with short retry on WinError 5 (issue #74)."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = digest_path(home)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    last_err: OSError | None = None
    for attempt in range(max(1, int(retries))):
        try:
            tmp.replace(path)
            return
        except OSError as e:
            last_err = e
            # WinError 5 / sharing violation — another reader holds digest.json
            if attempt + 1 < retries:
                time.sleep(float(backoff_s) * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err


def ensure_seats(doc: dict[str, Any]) -> dict[str, Any]:
    machines = doc.setdefault("machines", {})
    if not isinstance(machines, dict):
        machines = {}
        doc["machines"] = machines
    for mid in FLEET_MACHINE_IDS:
        machines[mid] = coerce_machine(mid, machines.get(mid))
    for mid in list(machines.keys()):
        if mid not in FLEET_MACHINE_IDS:
            machines[mid] = coerce_machine(mid, machines.get(mid))
    doc.setdefault("v", 1)
    doc.setdefault("events", [])
    if not isinstance(doc["events"], list):
        doc["events"] = []
    if not isinstance(doc.get("cursor_pools"), list):
        doc["cursor_pools"] = []
    if not isinstance(doc.get("queue"), dict):
        doc["queue"] = {"unaccepted": [], "accepted": [], "done": [], "workers": {}}
    return doc


def coerce_machine(mid: str, raw: Any) -> dict[str, Any]:
    base = empty_machine(mid)
    if not isinstance(raw, dict):
        return base
    online = bool(raw.get("online", False))
    base.update(
        {
            "nick": str(raw.get("nick") or base["nick"]),
            "shop": str(raw.get("shop") or base["shop"]),
            "online": online,
            "status": str(raw.get("status") or ("I am online" if online else "I am offline")),
            "working_on": str(raw.get("working_on") or ""),
            "workers": coerce_workers(mid, raw.get("workers")),
            "lastSeen": str(raw.get("lastSeen") or raw.get("last_seen") or ""),
        }
    )
    if isinstance(raw.get("pcent"), dict):
        base["pcent"] = raw["pcent"]
    for k in ("running", "queued", "uptime_since", "fuel", "agent", "model"):
        if k in raw and raw[k] is not None:
            base[k] = raw[k]
    try:
        base["running"] = int(base.get("running") or 0)
    except (TypeError, ValueError):
        base["running"] = 0
    try:
        base["queued"] = int(base.get("queued") or 0)
    except (TypeError, ValueError):
        base["queued"] = 0
    return base


def nick_keyed_machine_workers(workers: Any) -> dict[str, Any]:
    """FR #162: exactly one entry per worker nick; drop digit-only pid ghosts."""
    if not isinstance(workers, dict):
        return {}
    out: dict[str, Any] = {}
    for key, slot in workers.items():
        key_s = str(key)
        if key_s.isdigit():
            continue
        if isinstance(slot, dict):
            out[key_s] = dict(slot)
        else:
            out[key_s] = slot
    for key, slot in workers.items():
        key_s = str(key)
        if not key_s.isdigit() or not isinstance(slot, dict):
            continue
        nick = str(slot.get("nick") or "").strip()
        if not nick or nick in out:
            continue
        promoted = dict(slot)
        promoted.setdefault("nick", nick)
        promoted.setdefault("pid", key_s)
        out[nick] = promoted
    return out


def coerce_workers(mid: str, raw: Any) -> dict[str, Any]:
    """Normalize machines.<id>.workers to nick-keyed seats (FR #162).

    Ear/TipForm historically used pid keys (``{"43052": {pid, nick, ...}}``).
    FR #162 requires exactly one entry per present worker, keyed by nick and
    grouped under ``machines.<id>.workers`` — promote pid rows to their nick
    and drop digit-only ghost twins.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, ent in raw.items():
        e = ent if isinstance(ent, dict) else {}
        key_s = str(key)
        # FR #79 / K12 nick-keyed seat (job + working_on for TipForm)
        if ("-" in key_s and not key_s.isdigit()) or (
            "job" in e and "working_on" not in e and not key_s.isdigit()
        ):
            job = e.get("job")
            if job is None:
                job = e.get("working_on")
            state = str(e.get("state") or "idle")
            out[key_s] = {
                "state": state,
                "job": job,
                "working_on": str(job or "") if state.lower() == "busy" else str(e.get("working_on") or ""),
                "ts": str(e.get("ts") or ""),
            }
            if e.get("channel") is not None:
                out[key_s]["channel"] = e.get("channel")
            if e.get("nick"):
                out[key_s]["nick"] = e.get("nick")
            continue
        try:
            pid_s = str(int(str(key)))
        except ValueError:
            pid_s = key_s
        nick = str(e.get("nick") or f"{mid}-{pid_s}").strip()
        if nick in out:
            continue
        job = e.get("job")
        if job is None:
            job = e.get("working_on")
        state = str(e.get("state") or "running")
        out[nick] = {
            "state": state,
            "job": job,
            "working_on": str(e.get("working_on") or e.get("job") or ""),
            "ts": str(e.get("ts") or ""),
            "nick": nick,
            "pid": pid_s,
            "kind": str(e.get("kind") or ""),
            "agent": str(e.get("agent") or ""),
            "model": str(e.get("model") or ""),
        }
    return nick_keyed_machine_workers(out)


# FR #79: log once when nick cannot map to a machine
_unknown_machine_nicks_logged: set[str] = set()


def _log_unknown_worker_machine(nick: str, reason: str) -> None:
    import logging

    log = logging.getLogger("jeeves.digest")
    key = f"{nick}:{reason}"
    if key in _unknown_machine_nicks_logged:
        return
    _unknown_machine_nicks_logged.add(key)
    log.warning("worker_machine_unmapped nick=%s reason=%s", nick, reason)


def _refresh_machine_working_on(ent: dict[str, Any]) -> None:
    """K12 / FR #13: TipForm reads machines.<id>.working_on for START tiles.

    Prefer the newest busy worker's job/working_on; clear when none are busy.
    """
    workers = ent.get("workers") or {}
    if not isinstance(workers, dict):
        ent["working_on"] = ""
        return
    best_job = ""
    best_ts = ""
    for key, slot in workers.items():
        if not isinstance(slot, dict):
            continue
        # FR #162: digit-only keys are ghosts; skip if present
        if str(key).isdigit():
            continue
        if str(slot.get("state") or "").lower() != "busy":
            continue
        job = str(slot.get("job") or slot.get("working_on") or "").strip()
        if not job:
            continue
        ts = str(slot.get("ts") or "")
        if not best_job or ts >= best_ts:
            best_job = job
            best_ts = ts
    ent["working_on"] = best_job


def prune_machine_worker_ghosts(doc: dict[str, Any]) -> None:
    """In-place: nick-only workers under every machines.<id> (FR #162)."""
    machines = doc.get("machines")
    if not isinstance(machines, dict):
        return
    for mid, ent in list(machines.items()):
        if not isinstance(ent, dict):
            continue
        ent = dict(ent)
        ent["workers"] = nick_keyed_machine_workers(ent.get("workers"))
        _refresh_machine_working_on(ent)
        machines[mid] = ent


def mirror_top_worker_to_machine(
    doc: dict[str, Any],
    nick: str,
    entry: dict[str, Any] | None,
    *,
    remove: bool = False,
) -> bool:
    """FR #79 / #162: lockstep machines.<machine>.workers[nick] with top-level workers.

    One nick-keyed entry per present worker (no pid ghost twin). FR #13 / K12:
    also refresh machines.<id>.working_on so TipForm shows busy while hidden
    seat runs look idle in the TUI.
    """
    from .nicks import parse_worker_nick

    n = (nick or "").strip()
    if not n:
        return False
    parsed = parse_worker_nick(n)
    if not parsed:
        _log_unknown_worker_machine(n, "not_worker_nick")
        return False
    machine_part, pid = parsed
    mid = normalize_machine_id(machine_part)
    if not mid:
        _log_unknown_worker_machine(n, "empty_machine")
        return False
    machines = doc.setdefault("machines", {})
    if not isinstance(machines, dict):
        machines = {}
        doc["machines"] = machines
    ent = coerce_machine(mid, machines.get(mid))
    workers = dict(ent.get("workers") or {})
    if remove or entry is None:
        workers.pop(n, None)
        workers.pop(str(pid), None)  # drop legacy pid ghost if present
    else:
        job = entry.get("job")
        if job is None:
            job = entry.get("working_on")
        state = str(entry.get("state") or "idle")
        slot = {
            "state": state,
            "job": job,
            "working_on": str(job or "") if state.lower() == "busy" else "",
            "ts": str(entry.get("ts") or _utc_now()),
        }
        if entry.get("channel") is not None:
            slot["channel"] = entry.get("channel")
        workers[n] = slot
        # FR #162: never write pid ghost twin
        workers.pop(str(pid), None)
    ent["workers"] = nick_keyed_machine_workers(workers)
    _refresh_machine_working_on(ent)
    machines[mid] = coerce_machine(mid, ent)
    return True


def sync_all_top_workers_to_machines(doc: dict[str, Any], top_workers: dict[str, Any] | None) -> None:
    """GET heal: project top-level workers into machines.*.workers (add/update only)."""
    if not isinstance(top_workers, dict):
        return
    for nick, entry in top_workers.items():
        if isinstance(entry, dict):
            mirror_top_worker_to_machine(doc, str(nick), entry, remove=False)
    prune_machine_worker_ghosts(doc)


def _note_event(doc: dict[str, Any], kind: str, **fields: Any) -> None:
    ev = {"ts": _utc_now(), "kind": kind, **fields}
    events = doc.setdefault("events", [])
    if not isinstance(events, list):
        events = []
    events.append(ev)
    doc["events"] = events[-40:]


@dataclass
class CallbackOutcome:
    ok: bool
    err: str = ""
    changed: bool = True
    body: bytes | None = None
    actions: list[str] = field(default_factory=list)


def apply_report(home: Path, payload: dict[str, Any], *, briefer: str = "") -> CallbackOutcome:
    """Apply POST /bob/v1/report body (bobcallback-compatible ops + queue ops)."""
    if not isinstance(payload, dict):
        return CallbackOutcome(ok=False, err="malformed")
    # refuse secrets in body keys (never store). Do NOT scan title/job strings for ghp_
    # markers — FR titles may mention them (same rule as git secret-field filter).
    if any(str(k).lower() in ("secret", "x-bob-secret", "password", "token", "api_key") for k in payload):
        return CallbackOutcome(ok=False, err="secret")
    for k, v in payload.items():
        kl = str(k).lower()
        if kl.endswith("_secret") or kl.endswith("_token") or kl in ("authorization",):
            if isinstance(v, str) and looks_like_secret(v):
                return CallbackOutcome(ok=False, err="secret")

    op = str(payload.get("op") or "").strip().lower()
    doc = load_digest(home)

    # Keep queue mirror in digest for trays that read workers there
    from .queue import load_queue

    q = load_queue(home)
    doc["queue"] = {
        "unaccepted": list(q.get("unaccepted") or []),
        "accepted": list(q.get("accepted") or []),
        "done": list(q.get("done") or []),
        "workers": dict(q.get("workers") or {}),
    }

    if op in ("", "merge"):
        mid = normalize_machine_id(str(payload.get("machine") or payload.get("id") or ""))
        if not mid and op == "merge":
            return CallbackOutcome(ok=False, err="bad machine")
        if mid:
            before = deepcopy(doc["machines"].get(mid) or {})
            ent = coerce_machine(mid, doc["machines"].get(mid))
            # merge fields from payload
            if "online" in payload:
                ent["online"] = bool(payload["online"])
                ent["status"] = "I am online" if ent["online"] else "I am offline"
            if payload.get("status"):
                ent["status"] = str(payload["status"])
            if "working_on" in payload:
                ent["working_on"] = str(payload.get("working_on") or "")
            if isinstance(payload.get("pcent"), dict):
                ent["pcent"] = payload["pcent"]
            if payload.get("lastSeen") or payload.get("last_seen"):
                ent["lastSeen"] = str(payload.get("lastSeen") or payload.get("last_seen"))
            else:
                ent["lastSeen"] = _utc_now()
            if isinstance(payload.get("workers"), dict):
                ent["workers"] = coerce_workers(mid, payload["workers"])
            for k in ("running", "queued", "fuel", "uptime_since", "nick", "shop"):
                if k in payload and payload[k] is not None:
                    ent[k] = payload[k]
            # nested worker update by pid
            pid_raw = payload.get("pid")
            if pid_raw is not None and str(pid_raw) != "":
                try:
                    pid_s = str(int(str(pid_raw)))
                except ValueError:
                    return CallbackOutcome(ok=False, err="bad pid")
                w = ent.setdefault("workers", {})
                cur = dict(w.get(pid_s) or {})
                cur["pid"] = pid_s
                if "working_on" in payload:
                    cur["working_on"] = str(payload.get("working_on") or "")
                if payload.get("state"):
                    cur["state"] = str(payload["state"])
                if payload.get("nick"):
                    cur["nick"] = str(payload["nick"])
                if payload.get("agent"):
                    cur["agent"] = str(payload["agent"])
                if payload.get("model"):
                    cur["model"] = str(payload["model"])
                w[pid_s] = cur
                ent["workers"] = w
            doc["machines"][mid] = coerce_machine(mid, ent)
            if isinstance(payload.get("cursor_pools"), list):
                doc["cursor_pools"] = payload["cursor_pools"]
            if briefer:
                doc["briefer"] = briefer
            doc["ts"] = _utc_now()
            changed = doc["machines"][mid] != before or bool(payload.get("cursor_pools"))
            if changed:
                _note_event(doc, "merge", machine=mid)
                save_digest(home, doc)
            return CallbackOutcome(ok=True, changed=changed, actions=["merge"])

    if op == "worker_state":
        nick = str(payload.get("nick") or "")
        if nick:
            q = load_queue(home)
            ent = {
                "state": str(payload.get("state") or "idle"),
                "job": payload.get("job"),
                "ts": payload.get("ts") or _utc_now(),
            }
            q.setdefault("workers", {})[nick] = ent
            from .queue import save_queue

            save_queue(home, q)
            doc["queue"]["workers"] = q["workers"]
            doc["workers"] = dict(q["workers"])
            mirror_top_worker_to_machine(doc, nick, ent)
            doc["ts"] = _utc_now()
            save_digest(home, doc)
        return CallbackOutcome(ok=True, changed=bool(nick))

    if op == "queue_accept":
        from .queue import save_queue

        q = load_queue(home)
        row = payload.get("accepted_row")
        nick = str(payload.get("nick") or "")
        if isinstance(row, dict) and row.get("repo") and row.get("id"):
            rid = str(row.get("id"))
            rrepo = str(row.get("repo"))
            rtask = str(row.get("task") or "").upper()
            q["unaccepted"] = [
                r
                for r in (q.get("unaccepted") or [])
                if not (
                    str(r.get("repo")) == rrepo
                    and str(r.get("id")) == rid
                    and str(r.get("task") or "").upper() == rtask
                )
            ]
            q["accepted"] = [
                r
                for r in (q.get("accepted") or [])
                if not (
                    str(r.get("repo")) == rrepo
                    and str(r.get("id")) == rid
                    and str(r.get("task") or "").upper() == rtask
                )
            ]
            q.setdefault("accepted", []).append(row)
        if nick:
            ent = {
                "state": str(payload.get("state") or "busy"),
                "job": payload.get("job"),
                "ts": payload.get("ts") or _utc_now(),
            }
            q.setdefault("workers", {})[nick] = ent
            mirror_top_worker_to_machine(doc, nick, ent)
        save_queue(home, q)
        doc["queue"] = {
            "unaccepted": q.get("unaccepted") or [],
            "accepted": q.get("accepted") or [],
            "done": q.get("done") or [],
            "workers": q.get("workers") or {},
        }
        doc["workers"] = dict(q.get("workers") or {})
        doc["ts"] = _utc_now()
        save_digest(home, doc)
        return CallbackOutcome(ok=True)

    if op == "queue_done":
        from .queue import save_queue

        q = load_queue(home)
        nick = str(payload.get("nick") or "")
        if nick:
            ent = {
                "state": "idle",
                "job": None,
                "ts": payload.get("ts") or _utc_now(),
            }
            q.setdefault("workers", {})[nick] = ent
            save_queue(home, q)
            doc["queue"]["workers"] = q["workers"]
            doc["workers"] = dict(q["workers"])
            mirror_top_worker_to_machine(doc, nick, ent)
            doc["ts"] = _utc_now()
            save_digest(home, doc)
        return CallbackOutcome(ok=True, changed=bool(nick))

    if op == "shop-down":
        mid = normalize_machine_id(str(payload.get("machine") or payload.get("id") or ""))
        if not mid:
            return CallbackOutcome(ok=False, err="bad machine")
        ent = empty_machine(mid)
        doc["machines"][mid] = ent
        doc["ts"] = _utc_now()
        _note_event(doc, "shop-down", machine=mid)
        save_digest(home, doc)
        return CallbackOutcome(ok=True, actions=["shop-down"])

    if op == "delete-worker":
        mid = normalize_machine_id(str(payload.get("machine") or payload.get("id") or ""))
        pid = str(payload.get("pid") or "")
        nick = str(payload.get("nick") or "")
        if not mid or not (pid or nick):
            return CallbackOutcome(ok=False, err="bad delete")
        from .queue import load_queue, save_queue

        if not nick and pid:
            nick = f"{mid}-{pid}"
        ent = coerce_machine(mid, doc["machines"].get(mid))
        workers = dict(ent.get("workers") or {})
        if pid:
            workers.pop(str(pid), None)
            try:
                workers.pop(str(int(pid)), None)
            except ValueError:
                pass
            workers.pop(f"{mid}-{pid}", None)
        if nick:
            workers.pop(nick, None)
            mirror_top_worker_to_machine(doc, nick, None, remove=True)
            q = load_queue(home)
            qw = dict(q.get("workers") or {})
            qw.pop(nick, None)
            q["workers"] = qw
            save_queue(home, q)
            doc["queue"]["workers"] = qw
            doc["workers"] = dict(qw)
        ent["workers"] = workers
        doc["machines"][mid] = coerce_machine(mid, ent)
        doc["ts"] = _utc_now()
        _note_event(doc, "delete-worker", machine=mid, pid=pid or nick)
        save_digest(home, doc)
        return CallbackOutcome(ok=True, actions=["delete-worker"])

    if op == "git-claim":
        # claim_top lives in queue; return empty claim if none
        from .queue import top_unaccepted

        row = top_unaccepted(home)
        body = json.dumps({"ok": True, "claimed": row}, separators=(",", ":")).encode("utf-8")
        return CallbackOutcome(ok=True, changed=row is not None, body=body)

    # bare machine report without op=merge still accepted as merge
    if payload.get("machine") or payload.get("id"):
        payload = {**payload, "op": "merge"}
        return apply_report(home, payload, briefer=briefer)

    return CallbackOutcome(ok=False, err="bad op")


def public_digest_snapshot(home: Path, *, queue_home: Path | None = None) -> dict[str, Any]:
    """GET shape for trays/TipForm (+ queue mirror)."""
    doc = load_digest(home)
    qh = queue_home or home
    try:
        from .queue import load_queue

        q = load_queue(qh)
        doc["queue"] = {
            "unaccepted": list(q.get("unaccepted") or []),
            "accepted": list(q.get("accepted") or []),
            "done": list(q.get("done") or []),
            "workers": dict(q.get("workers") or {}),
        }
        # also top-level workers for older readers
        tw = dict(q.get("workers") or {})
        doc["workers"] = tw
        # FR #79: heal machines.<id>.workers so TipForm is not empty while top is busy
        sync_all_top_workers_to_machines(doc, tw)
        # FR #162: nick-only keys; drop stale pid ghosts even if top was empty
        prune_machine_worker_ghosts(doc)
    except Exception:
        prune_machine_worker_ghosts(doc)
    # FR #68: additive focus list (trays may ignore unknown keys)
    # FR #154: additive focus_strict boolean from focus.json (does not alter focus list)
    try:
        from .focus import focus_public_list, is_strict_focus

        doc["focus"] = focus_public_list(qh)
        doc["focus_strict"] = bool(is_strict_focus(qh))
    except Exception:
        doc.setdefault("focus", [])
        doc.setdefault("focus_strict", False)
    # version stamp optional
    stamp = Path(home) / "jeeves_version.json"
    if stamp.is_file():
        try:
            doc.update(json.loads(stamp.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return ensure_seats(doc)
