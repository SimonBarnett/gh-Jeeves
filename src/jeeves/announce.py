"""GitHub → length-safe GIT announce (FR #24) + secret-field filter.

Vital fields first (fixed order), title last and only truncated. Budget is
UTF-8 **bytes** against a real IRC PRIVMSG line (512 incl. prefix + CRLF).
Never emit a continuation line that a parser needs. Pre-send validates with
the same parser. Queue claims come from the webhook payload, not IRC text.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from .queue import Claim, claim_from_payload

# Classic IRC: 512 bytes including CRLF.
IRC_LINE_MAX_BYTES = 512
DEFAULT_NICK = "Jeeves"
DEFAULT_USERHOST = "jeeves@ionos"
DEFAULT_CHANNEL = "#bobiverse"
DEFAULT_PRIVMSG_TEXT_MAX = 400

GIT_PREFIX = "GIT"

# Markers that look like secrets — scanned only on secret-bearing fields.
_SECRET_MARKERS = (
    "BEGIN PRIVATE KEY",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "github_pat_",
    "xoxb-",
    "xoxp-",
    "sk-or-",
    "sk-ant-",
)

_SECRET_FIELDS = frozenset(
    {
        "authorization",
        "token",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "ssh_key",
        "api_key",
        "access_token",
        "refresh_token",
    }
)

_SECRETISH_TITLE = re.compile(
    r"(?i)("
    r"password\s*=\s*\S+"
    r"|api[_-]?key\s*=\s*\S+"
    r"|secret\s*=\s*\S+"
    r"|token\s*=\s*\S+"
    r"|bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|sk-[A-Za-z0-9]{10,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]+"
    r"|\.env\b"
    r")"
)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class VitalFields:
    """Fields the next step must always see on one line (never split)."""

    event: str
    task: str  # FR | MRB | UAT | PING | PUSH | OTHER
    ref: str  # owner/repo#n or owner/repo@branch
    action: str = ""
    url: str = ""
    fixes: str = ""
    head: str = ""
    title: str = ""
    actor: str = ""

    def as_map(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name) or "") for f in fields(self)}


@dataclass
class FormatResult:
    line: str
    vital: VitalFields
    compact: bool = False
    truncated_title: bool = False
    ok: bool = True
    error: str = ""
    wire_bytes: int = 0

    @property
    def body(self) -> str:
        return self.line


@dataclass
class QueueEvent:
    kind: str
    vital: dict[str, str]
    reason: str = ""


def utf8_len(s: str) -> int:
    return len((s or "").encode("utf-8"))


def truncate_utf8(s: str, max_bytes: int, ellipsis: str = "...") -> str:
    if max_bytes < 1:
        return ""
    raw = s or ""
    if utf8_len(raw) <= max_bytes:
        return raw
    ell_b = utf8_len(ellipsis)
    if ell_b >= max_bytes:
        out: list[str] = []
        used = 0
        for ch in raw:
            cb = utf8_len(ch)
            if used + cb > max_bytes:
                break
            out.append(ch)
            used += cb
        return "".join(out)
    budget = max_bytes - ell_b
    out = []
    used = 0
    for ch in raw:
        cb = utf8_len(ch)
        if used + cb > budget:
            break
        out.append(ch)
        used += cb
    return "".join(out) + ellipsis


def sanitize_title(title: str) -> str:
    t = (title or "").replace("\r", " ").replace("\n", " ")
    t = _CTRL.sub("", t)
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t).strip()
    if _SECRETISH_TITLE.search(t):
        t = _SECRETISH_TITLE.sub("[redacted]", t)
    return t


def irc_prefix_bytes(
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
) -> int:
    prefix = f":{nick}!{userhost} PRIVMSG {channel} :"
    return utf8_len(prefix) + 2


def text_budget(
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
    line_max: int = IRC_LINE_MAX_BYTES,
    floor: int = 64,
) -> int:
    room = line_max - irc_prefix_bytes(nick, userhost, channel)
    if room < floor:
        room = floor
    if line_max <= IRC_LINE_MAX_BYTES:
        room = min(room, DEFAULT_PRIVMSG_TEXT_MAX)
    return room


def body_budget(*, irc_max: int = IRC_LINE_MAX_BYTES, **kwargs: Any) -> int:
    """Alias used by skills/tests."""
    return text_budget(line_max=irc_max, **kwargs)


def wire_line_bytes(
    text: str,
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
) -> int:
    return irc_prefix_bytes(nick, userhost, channel) + utf8_len(text)


def short_url(url: str, max_bytes: int = 64) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
        compact = f"{p.netloc}{p.path}" if p.netloc else u
    except Exception:
        compact = u
    return truncate_utf8(compact, max_bytes, ellipsis="")


def _join(parts: Iterable[str]) -> str:
    return " ".join(p for p in parts if p)


def secret_marker_hit(text: str) -> str | None:
    if not text:
        return None
    upper = text if len(text) < 500_000 else text[:500_000]
    for m in _SECRET_MARKERS:
        if m in upper:
            return m
    return None


def _walk_secret_fields(obj: Any, path: str = "") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            child = f"{path}.{key}" if path else key
            if key in _SECRET_FIELDS or key.endswith("_token") or key.endswith("_secret"):
                if isinstance(v, str):
                    hit = secret_marker_hit(v)
                    if hit:
                        return hit
                elif isinstance(v, (dict, list)):
                    hit = _walk_secret_fields(v, child)
                    if hit:
                        return hit
            else:
                hit = _walk_secret_fields(v, child)
                if hit:
                    return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hit = _walk_secret_fields(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def payload_secret_rejected(payload: dict) -> str | None:
    """Return marker if secret-bearing fields contain a marker; ignore title/body mentions."""
    return _walk_secret_fields(payload)


def infer_task(event: str, action: str, *, merged: bool | None = None) -> str:
    ev = (event or "").strip().lower()
    act = (action or "").strip().lower()
    if ev == "ping":
        return "PING"
    if ev == "push":
        return "PUSH"
    if ev == "issues":
        return "FR"
    if ev == "pull_request":
        if act == "closed" and merged:
            return "UAT"
        return "MRB"
    return "OTHER"


def format_vital_core(v: VitalFields, *, title: str = "") -> str:
    """Fixed order: GIT event task ref action url [fixes:] [head:] [by actor] [title]."""
    bits = [GIT_PREFIX, v.event, v.task, v.ref]
    if v.action:
        bits.append(v.action)
    if v.url:
        bits.append(v.url)
    if v.fixes:
        bits.append(f"fixes:{v.fixes}")
    if v.head:
        bits.append(f"head:{v.head}")
    if v.actor:
        bits.append(f"by:{v.actor}")
    if title:
        bits.append(title)
    return _join(bits)


def format_compact(v: VitalFields) -> str:
    """GIT <task> <ref> <short-url> when vitals alone overflow."""
    su = short_url(v.url) if v.url else ""
    return _join([GIT_PREFIX, v.task, v.ref, su or v.action or v.event])


def parse_announce(line: str) -> VitalFields | None:
    """Parse a single GIT announce body. Same parser workers/validate use."""
    text = (line or "").strip()
    if text.upper().startswith("PRIVMSG ") and " :" in text:
        text = text.split(" :", 1)[1].strip()
    if not text.upper().startswith(GIT_PREFIX + " "):
        return None
    parts = text.split()
    if len(parts) < 2:
        return None

    # Full: GIT event task ref ...
    # Compact: GIT task ref ...
    # Legacy-ish G1-era: GIT event repo action #n title — still parse best-effort
    if len(parts) >= 4 and parts[2].upper() in ("FR", "MRB", "UAT", "PING", "PUSH", "OTHER"):
        event, task, ref = parts[1], parts[2].upper(), parts[3]
        rest = parts[4:]
    elif len(parts) >= 3 and parts[1].upper() in ("FR", "MRB", "UAT", "PING", "PUSH", "OTHER"):
        event = ""
        task, ref = parts[1].upper(), parts[2]
        rest = parts[3:]
    else:
        event = parts[1] if len(parts) > 1 else ""
        task = infer_task(event, parts[3] if len(parts) > 3 else "")
        # legacy: GIT issues repo action #n
        repo = parts[2] if len(parts) > 2 else ""
        action = ""
        num = ""
        rest = parts[3:]
        if rest and rest[0].isalpha():
            action = rest[0]
            rest = rest[1:]
        if rest and rest[0].startswith("#"):
            num = rest[0].lstrip("#")
            rest = rest[1:]
        ref = f"{repo}#{num}" if repo and num else repo
        title = sanitize_title(" ".join(rest))
        return VitalFields(event=event, task=task, ref=ref, action=action, title=title)

    action = ""
    url = ""
    fixes = ""
    head = ""
    actor = ""
    title_parts: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        low = tok.lower()
        if low.startswith("fixes:"):
            fixes = tok.split(":", 1)[1]
            i += 1
            continue
        if low.startswith("head:"):
            head = tok.split(":", 1)[1]
            i += 1
            continue
        if low.startswith("by:"):
            actor = tok.split(":", 1)[1]
            i += 1
            continue
        if not action and tok.replace("_", "").isalpha() and not tok.startswith("http"):
            action = tok
            i += 1
            continue
        if not url and (
            tok.startswith("http://")
            or tok.startswith("https://")
            or tok.startswith("github.com")
        ):
            url = tok if tok.startswith("http") else "https://" + tok
            i += 1
            continue
        title_parts = rest[i:]
        break
    if not event:
        event = {
            "FR": "issues",
            "MRB": "pull_request",
            "UAT": "pull_request",
            "PING": "ping",
            "PUSH": "push",
        }.get(task, "other")
    return VitalFields(
        event=event,
        task=task,
        ref=ref,
        action=action,
        url=url,
        fixes=fixes,
        head=head,
        title=sanitize_title(" ".join(title_parts)) if title_parts else "",
        actor=actor,
    )


parse_git_announce = parse_announce  # skill alias


def vital_core_equal(a: VitalFields, b: VitalFields) -> bool:
    for k in ("task", "ref", "action", "fixes", "head"):
        if str(getattr(a, k) or "") != str(getattr(b, k) or ""):
            return False
    # event: allow compact recovery
    ae, be = (a.event or "").lower(), (b.event or "").lower()
    if ae and be and ae != be:
        return False
    au, bu = a.url or "", b.url or ""
    if au and bu:
        if au.rstrip("/") == bu.rstrip("/"):
            pass
        elif short_url(au) == short_url(bu):
            pass
        elif au.split("/")[-2:] == bu.split("/")[-2:]:
            pass
        else:
            return False
    return True


def format_announce(
    vital: VitalFields,
    *,
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
    line_max: int = IRC_LINE_MAX_BYTES,
) -> FormatResult:
    v = VitalFields(
        event=(vital.event or "").strip().lower() or "other",
        task=(vital.task or "OTHER").strip().upper(),
        ref=(vital.ref or "").strip(),
        action=(vital.action or "").strip(),
        url=(vital.url or "").strip(),
        fixes=(vital.fixes or "").strip(),
        head=(vital.head or "").strip(),
        title=sanitize_title(vital.title or ""),
        actor=(vital.actor or "").strip(),
    )
    budget = text_budget(nick, userhost, channel, line_max=line_max)
    core = format_vital_core(v, title="")
    if utf8_len(core) > budget:
        compact = truncate_utf8(format_compact(v), budget)
        parsed = parse_announce(compact)
        ok = parsed is not None and bool(parsed.task and parsed.ref)
        return FormatResult(
            line=compact,
            vital=v,
            compact=True,
            truncated_title=True,
            ok=ok,
            error="" if ok else "compact-overflow",
            wire_bytes=wire_line_bytes(compact, nick, userhost, channel),
        )

    title = v.title
    truncated = False
    room = budget - utf8_len(core) - (1 if title else 0)
    if title and room < 1:
        title = ""
        truncated = True
    elif title and utf8_len(title) > room:
        title = truncate_utf8(title, room)
        truncated = True

    line = format_vital_core(v, title=title)
    while wire_line_bytes(line, nick, userhost, channel) > line_max and title:
        room = max(0, room - 8)
        title = truncate_utf8(v.title, room) if room else ""
        truncated = True
        line = format_vital_core(v, title=title)
        if not title:
            break

    if wire_line_bytes(line, nick, userhost, channel) > line_max:
        compact = truncate_utf8(format_compact(v), budget)
        return FormatResult(
            line=compact,
            vital=v,
            compact=True,
            truncated_title=True,
            ok=True,
            wire_bytes=wire_line_bytes(compact, nick, userhost, channel),
        )

    parsed = parse_announce(line)
    ok = parsed is not None and vital_core_equal(v, parsed)
    return FormatResult(
        line=line,
        vital=v,
        compact=False,
        truncated_title=truncated,
        ok=ok,
        error="" if ok else "round-trip-mismatch",
        wire_bytes=wire_line_bytes(line, nick, userhost, channel),
    )


def vital_from_github(event: str, payload: Mapping[str, Any]) -> VitalFields:
    ev = (event or "").strip().lower()
    repo_obj = payload.get("repository") if isinstance(payload, Mapping) else None
    repo = ""
    if isinstance(repo_obj, dict):
        repo = str(repo_obj.get("full_name") or repo_obj.get("name") or "").strip()
    action = str(payload.get("action") or "").strip()
    actor = str((payload.get("sender") or {}).get("login") or "") if isinstance(payload, Mapping) else ""
    url = ""
    title = ""
    fixes = ""
    head = ""
    merged = None
    ref = repo

    if ev == "issues":
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        num = issue.get("number")
        title = str(issue.get("title") or "")
        url = str(issue.get("html_url") or "")
        if not url and repo and num is not None:
            url = f"https://github.com/{repo}/issues/{num}"
        if repo and num is not None:
            ref = f"{repo}#{num}"
    elif ev == "pull_request":
        pr = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
        num = pr.get("number")
        title = str(pr.get("title") or "")
        url = str(pr.get("html_url") or "")
        if not url and repo and num is not None:
            url = f"https://github.com/{repo}/pull/{num}"
        merged = bool(pr.get("merged"))
        if action == "closed" and merged:
            action = "merged"
        head_obj = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        head = str(head_obj.get("ref") or "")
        body = str(pr.get("body") or "")
        m = re.search(
            r"(?i)(?:fixes|closes|resolves)\s+#?(\d+)|(?:fixes|closes|resolves)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)",
            body,
        )
        if m:
            if m.group(2):
                fixes = m.group(2)
            elif m.group(1) and repo:
                fixes = f"{repo}#{m.group(1)}"
        if repo and num is not None:
            ref = f"{repo}#{num}"
    elif ev == "push":
        r = str(payload.get("ref") or "")
        if r.startswith("refs/heads/"):
            r = r[len("refs/heads/") :]
        after = str(payload.get("after") or "")[:12]
        ref = f"{repo}@{r}" if repo and r else repo or r
        title = f"{after} push" if after else "push"
        url = str(payload.get("compare") or "")
        action = "push"
    elif ev == "ping":
        ref = repo or "ping"
        title = str(payload.get("zen") or "ping")
        action = "ping"

    task = infer_task(ev, action, merged=merged)
    return VitalFields(
        event=ev or "other",
        task=task,
        ref=ref,
        action=action,
        url=url,
        fixes=fixes,
        head=head,
        title=title,
        actor=actor,
    )


def format_github_webhook_announce(event: str, payload: dict) -> str | None:
    """Public API for G1/receiver: one length-safe GIT body line."""
    if not isinstance(payload, dict):
        return None
    res = format_announce(vital_from_github(event, payload))
    if not res.ok and not res.line:
        return None
    return res.line


def format_git_announce(
    *,
    event: str,
    repo: str,
    number: int | str | None = None,
    action: str = "",
    url: str = "",
    title: str = "",
    task: str = "",
    fixes: str = "",
    head: str = "",
    actor: str = "",
    irc_max: int = IRC_LINE_MAX_BYTES,
    **kwargs: Any,
) -> FormatResult:
    """Skill/test helper."""
    num = str(number).lstrip("#") if number is not None else ""
    ref = f"{repo}#{num}" if repo and num else repo
    t = task or infer_task(event, action)
    v = VitalFields(
        event=event,
        task=t,
        ref=ref,
        action=action,
        url=url or (f"https://github.com/{repo}/issues/{num}" if num else ""),
        fixes=fixes,
        head=head,
        title=title,
        actor=actor,
    )
    return format_announce(v, line_max=irc_max, **kwargs)


def validate_before_send(
    result: FormatResult,
    *,
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
    line_max: int = IRC_LINE_MAX_BYTES,
) -> tuple[bool, str]:
    if not result.line:
        return False, result.error or "empty"
    if wire_line_bytes(result.line, nick, userhost, channel) > line_max:
        return False, "wire-too-long"
    parsed = parse_announce(result.line)
    if parsed is None:
        return False, "unparseable"
    if not result.compact and not vital_core_equal(result.vital, parsed):
        return False, "vital-mismatch"
    if result.compact and not (parsed.task and parsed.ref):
        return False, "compact-incomplete"
    return True, ""


def validate_round_trip(result: FormatResult, **kwargs: Any) -> bool:
    ok, _ = validate_before_send(result, **kwargs)
    return ok


def append_queue_event(home: Path | None, ev: QueueEvent) -> None:
    """Persist queue side-channel (webhook path); independent of IRC send."""
    if home is None:
        return
    root = Path(home) / "queue_events"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}_{ev.kind}.json"
    (root / name).write_text(
        json.dumps({"kind": ev.kind, "vital": ev.vital, "reason": ev.reason}, indent=2) + "\n",
        encoding="utf-8",
    )


class MemoryQueue:
    def __init__(self) -> None:
        self.items: list[QueueEvent] = []

    def append(self, ev: QueueEvent) -> None:
        self.items.append(ev)

    def has_ref(self, ref: str) -> bool:
        return any(i.vital.get("ref") == ref for i in self.items if i.kind == "git")


def prepare_send(
    vital: VitalFields,
    *,
    queue_append: Callable[[QueueEvent], None] | None = None,
    home: Path | None = None,
    log: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> tuple[str | None, FormatResult]:
    log = log or (lambda _m: None)
    res = format_announce(vital, **kwargs)
    q = QueueEvent(kind="git", vital=res.vital.as_map(), reason="webhook")
    if queue_append:
        queue_append(q)
    append_queue_event(home, q)
    ok, err = validate_before_send(
        res,
        **{k: kwargs[k] for k in ("nick", "userhost", "channel", "line_max") if k in kwargs},
    )
    if not ok:
        log(f"ERROR announce validate failed: {err} line={res.line!r}")
        err_ev = QueueEvent(kind="announce_error", vital=res.vital.as_map(), reason=err)
        if queue_append:
            queue_append(err_ev)
        append_queue_event(home, err_ev)
        return None, res
    return res.line, res


def simulate_417(line: str, *, log: Callable[[str], None] | None = None, home: Path | None = None) -> str:
    log = log or (lambda _m: None)
    preview = (line or "")[:80]
    msg = f"ERROR 417 line too long preview={preview!r}"
    log(msg)
    append_queue_event(
        home,
        QueueEvent(kind="irc_417", vital={"preview": preview}, reason="417"),
    )
    return msg


handle_simulated_417 = simulate_417


def format_offer_line(
    machine: str,
    vital: VitalFields,
    *,
    nick: str = "bob-marchhare",
    **kwargs: Any,
) -> FormatResult:
    ch = f"#{machine}" if not machine.startswith("#") else machine
    base = VitalFields(
        event="offer",
        task=vital.task,
        ref=vital.ref,
        action="OFFER",
        url=vital.url,
        fixes=vital.fixes,
        head=vital.head,
        title=vital.title,
    )
    return format_announce(base, nick=nick, channel=ch, **kwargs)


def format_list_row(
    vital: VitalFields,
    *,
    pos: int = 1,
    mode: str = "unaccepted",
    nick: str = DEFAULT_NICK,
    **kwargs: Any,
) -> FormatResult:
    v = VitalFields(
        event="list",
        task=vital.task,
        ref=vital.ref,
        action=mode,
        url=vital.url,
        fixes=vital.fixes,
        head=vital.head,
        title=f"#{pos} {vital.title}".strip(),
    )
    return format_announce(v, nick=nick, channel="#pm", **kwargs)


def process_git_webhook(
    event: str,
    payload: dict,
    *,
    home: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[str | None, Claim | None, str | None]:
    """
    Returns (announce_line, claim, reject_reason).
    reject_reason set → do not announce.
    Claim always comes from payload (queue never depends on IRC text).
    """
    log = log or (lambda _m: None)
    hit = payload_secret_rejected(payload)
    if hit:
        return None, None, f"secret_field:{hit}"
    claim = claim_from_payload(event, payload)
    vital = vital_from_github(event, payload)
    line, res = prepare_send(vital, home=home, log=log)
    if line is None:
        # still return claim so queue can proceed; announce suppressed
        log(f"ERROR announce suppressed; queue claim kept ref={vital.ref}")
        return None, claim, res.error or "announce_validate"
    return line, claim, None
