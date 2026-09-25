"""FR #24: length-safe machine-read announcements.

Vital fields first (fixed order), title last and only truncated. Budget is
UTF-8 **bytes** against a real IRC PRIVMSG line (512 incl. prefix + CRLF).
Never emit a continuation line that a parser needs. Pre-send validates with
the same parser workers use.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

# Classic IRC: 512 bytes including CRLF. PRIVMSG wire:
#   :nick!user@host PRIVMSG #chan :text\r\n
IRC_LINE_MAX_BYTES = 512
DEFAULT_NICK = "Jeeves"
DEFAULT_USERHOST = "jeeves@ionos"
DEFAULT_CHANNEL = "#bobiverse"
# Ergo may advertise larger LINELEN; callers can raise text budget via env later.
DEFAULT_PRIVMSG_TEXT_MAX = 400

GIT_PREFIX = "GIT"
COMPACT_MARKER = "compact"

# Secret-filter trip strings (K14 / agentic_irc #206 style) — never put in title.
# Match value-bearing forms first so password=hunter2 is fully redacted.
_SECRETISH = re.compile(
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
    fixes: str = ""  # owner/repo#m
    head: str = ""  # PR head branch
    title: str = ""

    def as_map(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name) or "") for f in fields(self)}


@dataclass
class FormatResult:
    line: str  # body only (no PRIVMSG wrapper)
    vital: VitalFields
    compact: bool = False
    truncated_title: bool = False
    ok: bool = True
    error: str = ""
    wire_bytes: int = 0


@dataclass
class QueueEvent:
    """Queue never depends on IRC text — webhook payload drives this."""

    kind: str
    vital: dict[str, str]
    payload_keys: list[str] = field(default_factory=list)
    reason: str = ""


def utf8_len(s: str) -> int:
    return len((s or "").encode("utf-8"))


def truncate_utf8(s: str, max_bytes: int, ellipsis: str = "...") -> str:
    """Truncate to max_bytes of UTF-8; never cut mid-codepoint. Ellipsis if cut."""
    if max_bytes < 1:
        return ""
    raw = s or ""
    if utf8_len(raw) <= max_bytes:
        return raw
    ell_b = utf8_len(ellipsis)
    if ell_b >= max_bytes:
        # extreme budget: drop ellipsis, keep whole codepoints only
        out = []
        used = 0
        for ch in raw:
            cb = utf8_len(ch)
            if used + cb > max_bytes:
                break
            out.append(ch)
            used += cb
        return "".join(out)
    budget = max_bytes - ell_b
    out: list[str] = []
    used = 0
    for ch in raw:
        cb = utf8_len(ch)
        if used + cb > budget:
            break
        out.append(ch)
        used += cb
    return "".join(out) + ellipsis


def sanitize_title(title: str) -> str:
    """No CR/LF/controls; collapse space; strip secretish tokens (K14)."""
    t = (title or "").replace("\r", " ").replace("\n", " ")
    t = _CTRL.sub("", t)
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t).strip()
    if _SECRETISH.search(t):
        t = _SECRETISH.sub("[redacted]", t)
    return t


def irc_prefix_bytes(
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
) -> int:
    """Bytes of `:nick!user@host PRIVMSG #chan :` + CRLF (excluding text)."""
    prefix = f":{nick}!{userhost} PRIVMSG {channel} :"
    return utf8_len(prefix) + 2  # CRLF


def text_budget(
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
    line_max: int = IRC_LINE_MAX_BYTES,
    floor: int = 64,
) -> int:
    """Max UTF-8 bytes allowed in the PRIVMSG text body."""
    overhead = irc_prefix_bytes(nick, userhost, channel)
    room = line_max - overhead
    if room < floor:
        room = floor
    # also respect a soft DEFAULT_PRIVMSG_TEXT_MAX when line_max is classic 512
    if line_max <= IRC_LINE_MAX_BYTES:
        room = min(room, DEFAULT_PRIVMSG_TEXT_MAX)
    return room


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
        path = p.path or ""
        # github.com/o/r/issues/n -> keep host + path
        compact = f"{p.netloc}{path}" if p.netloc else u
    except Exception:
        compact = u
    return truncate_utf8(compact, max_bytes, ellipsis="")


def infer_task(event: str, action: str, *, merged: bool | None = None) -> str:
    ev = (event or "").strip().lower()
    act = (action or "").strip().lower()
    if ev == "ping":
        return "PING"
    if ev == "push":
        return "PUSH"
    if ev == "issues":
        return "FR"
    if ev in ("pull_request", "pull_request_review"):
        if act == "closed" and merged:
            return "UAT"
        if act in ("opened", "reopened", "synchronize", "ready_for_review"):
            return "MRB"
        if act == "closed":
            return "MRB"
        return "MRB"
    return "OTHER"


def _join_tokens(parts: Iterable[str]) -> str:
    return " ".join(p for p in parts if p)


def format_vital_core(v: VitalFields, *, include_title: bool, title: str = "") -> str:
    """Fixed order: event task ref action url [fixes] [head] [title]."""
    bits = [GIT_PREFIX, v.event, v.task, v.ref]
    if v.action:
        bits.append(v.action)
    if v.url:
        bits.append(v.url)
    if v.fixes:
        bits.append(f"fixes:{v.fixes}")
    if v.head:
        bits.append(f"head:{v.head}")
    if include_title and title:
        bits.append(title)
    return _join_tokens(bits)


def format_compact(v: VitalFields) -> str:
    """When vital fields alone exceed budget: GIT <task> <ref> <short-url>."""
    su = short_url(v.url) if v.url else ""
    return _join_tokens([GIT_PREFIX, v.task, v.ref, su or v.action or v.event])


_REF_RE = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:#(?P<num>\d+)|@(?P<br>\S+))?$")


def parse_announce(line: str) -> VitalFields | None:
    """Parse a single GIT announce body (no PRIVMSG wrapper). Same parser as workers."""
    text = (line or "").strip()
    if text.upper().startswith("PRIVMSG "):
        if " :" in text:
            text = text.split(" :", 1)[1].strip()
    if not text.upper().startswith(GIT_PREFIX + " ") and text.upper() != GIT_PREFIX:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    # Full: GIT event task ref [action] [url] [fixes:] [head:] [title...]
    # Compact: GIT task ref [short-url|action|event]
    if len(parts) >= 4 and parts[2] in ("FR", "MRB", "UAT", "PING", "PUSH", "OTHER"):
        event, task, ref = parts[1], parts[2], parts[3]
        rest = parts[4:]
    elif len(parts) >= 3 and parts[1] in ("FR", "MRB", "UAT", "PING", "PUSH", "OTHER"):
        # compact
        event = ""
        task, ref = parts[1], parts[2]
        rest = parts[3:]
    else:
        # legacy-ish: GIT event repo ...
        event = parts[1] if len(parts) > 1 else ""
        task = "OTHER"
        ref = parts[2] if len(parts) > 2 else ""
        rest = parts[3:]

    action = ""
    url = ""
    fixes = ""
    head = ""
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
        if not action and not tok.startswith("http") and not tok.startswith("github.com") and "#" not in tok[:3]:
            # first non-url token may be action (opened/merged/...)
            if tok.isalpha() or tok.replace("_", "").isalpha():
                action = tok
                i += 1
                continue
        if not url and (tok.startswith("http://") or tok.startswith("https://") or tok.startswith("github.com")):
            url = tok if tok.startswith("http") else "https://" + tok
            i += 1
            continue
        title_parts = rest[i:]
        break
    title = sanitize_title(" ".join(title_parts)) if title_parts else ""
    if not event and task:
        # compact form: recover event from task heuristic
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
        title=title,
    )


def vital_core_equal(a: VitalFields, b: VitalFields) -> bool:
    """Compare fields the next step needs (title may be truncated)."""
    keys = ("event", "task", "ref", "action", "url", "fixes", "head")
    for k in keys:
        av = str(getattr(a, k) or "")
        bv = str(getattr(b, k) or "")
        if k == "url":
            # compact may shorten URL — require same path tail if both set
            if not av or not bv:
                if bool(av) != bool(bv) and av and bv:
                    return False
                continue
            if av.rstrip("/") == bv.rstrip("/"):
                continue
            if short_url(av) == short_url(bv):
                continue
            # path suffix match (issues/24)
            if av.split("/")[-2:] == bv.split("/")[-2:]:
                continue
            return False
        if av != bv:
            # compact may leave event derived
            if k == "event" and av and bv:
                if {av, bv} <= {"issues", "pull_request", "ping", "push", "other"}:
                    # allow empty recovery only when one side empty
                    pass
            if k == "event" and (not av or not bv):
                continue
            if av != bv:
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
    """Build one length-safe body line. Only the title is truncated."""
    v = VitalFields(
        event=(vital.event or "").strip().lower() or "other",
        task=(vital.task or "OTHER").strip().upper(),
        ref=(vital.ref or "").strip(),
        action=(vital.action or "").strip(),
        url=(vital.url or "").strip(),
        fixes=(vital.fixes or "").strip(),
        head=(vital.head or "").strip(),
        title=sanitize_title(vital.title or ""),
    )
    budget = text_budget(nick, userhost, channel, line_max=line_max)
    core_no_title = format_vital_core(v, include_title=False)
    core_bytes = utf8_len(core_no_title)

    if core_bytes > budget:
        compact = format_compact(v)
        if utf8_len(compact) > budget:
            compact = truncate_utf8(compact, budget)
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
    # room for space + title
    room = budget - core_bytes - (1 if title else 0)
    if title and room < 1:
        title = ""
        truncated = True
    elif title and utf8_len(title) > room:
        title = truncate_utf8(title, room)
        truncated = True

    line = format_vital_core(v, include_title=bool(title), title=title)
    # hard assert wire size
    while wire_line_bytes(line, nick, userhost, channel) > line_max and title:
        room = max(0, room - 8)
        title = truncate_utf8(v.title, room) if room else ""
        truncated = True
        line = format_vital_core(v, include_title=bool(title), title=title)
        if not title:
            break

    if wire_line_bytes(line, nick, userhost, channel) > line_max:
        compact = format_compact(v)
        compact = truncate_utf8(compact, budget)
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
    """Map a GitHub webhook payload to vital fields (FR #24 order)."""
    ev = (event or "").strip().lower()
    repo_obj = payload.get("repository") if isinstance(payload, Mapping) else None
    repo = ""
    if isinstance(repo_obj, dict):
        repo = str(repo_obj.get("full_name") or repo_obj.get("name") or "").strip()
    action = str(payload.get("action") or "").strip()
    url = ""
    num = None
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
        if repo and num is not None:
            ref = f"{repo}#{num}"
    elif ev == "pull_request":
        pr = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
        num = pr.get("number")
        title = str(pr.get("title") or "")
        url = str(pr.get("html_url") or "")
        merged = pr.get("merged")
        if isinstance(merged, bool):
            pass
        else:
            merged = bool(merged)
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
        url = str((payload.get("compare") or "")) if isinstance(payload, Mapping) else ""
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
    )


def format_github_announce(
    event: str,
    payload: Mapping[str, Any],
    **kwargs: Any,
) -> FormatResult:
    return format_announce(vital_from_github(event, payload), **kwargs)


def format_offer_line(
    machine: str,
    vital: VitalFields,
    *,
    nick: str = "bob-marchhare",
    channel: str | None = None,
    **kwargs: Any,
) -> FormatResult:
    """Ear OFFER/ASSIGN body: still vital-first, single line."""
    ch = channel or f"#{machine}"
    # OFFER FR owner/repo#n url title
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
    # Reuse formatter but prefix OFFER for ears — encode as event=offer action=OFFER
    res = format_announce(base, nick=nick, channel=ch, **kwargs)
    # Replace GIT offer OFFER → OFFER for wire clarity while keeping parseable GIT form optional
    # Keep GIT form so one parser works: GIT offer FR ref OFFER url ...
    return res


def format_list_row(
    vital: VitalFields,
    *,
    pos: int = 1,
    mode: str = "unaccepted",
    nick: str = DEFAULT_NICK,
    channel: str = "nick",  # PM target treated as short
    **kwargs: Any,
) -> FormatResult:
    """!list PM row: one line, vital fields, title last."""
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
    return format_announce(v, nick=nick, channel=channel if channel.startswith("#") else f"#{channel}", **kwargs)


def validate_before_send(
    result: FormatResult,
    *,
    nick: str = DEFAULT_NICK,
    userhost: str = DEFAULT_USERHOST,
    channel: str = DEFAULT_CHANNEL,
    line_max: int = IRC_LINE_MAX_BYTES,
) -> tuple[bool, str]:
    """Pre-send: parse round-trip + wire size. On failure caller must not send."""
    if not result.line or not result.ok:
        return False, result.error or "format-failed"
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


def prepare_send(
    vital: VitalFields,
    *,
    queue_append: Callable[[QueueEvent], None] | None = None,
    log: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> tuple[str | None, FormatResult]:
    """Format + validate. Always enqueue queue event from vital (webhook path).

    Returns (line_or_None, result). None line means do not IRC-send; queue still written.
    """
    log = log or (lambda _m: None)
    res = format_announce(vital, **kwargs)
    q = QueueEvent(
        kind="git",
        vital=res.vital.as_map(),
        payload_keys=list(res.vital.as_map().keys()),
        reason="webhook",
    )
    if queue_append:
        queue_append(q)
    ok, err = validate_before_send(res, **{k: kwargs[k] for k in ("nick", "userhost", "channel", "line_max") if k in kwargs})
    if not ok:
        log(f"ERROR announce validate failed: {err} line={res.line!r}")
        if queue_append:
            queue_append(
                QueueEvent(
                    kind="announce_error",
                    vital=res.vital.as_map(),
                    reason=err,
                )
            )
        return None, res
    return res.line, res


class MemoryQueue:
    """Test double: queue items from webhook, independent of IRC send."""

    def __init__(self) -> None:
        self.items: list[QueueEvent] = []

    def append(self, ev: QueueEvent) -> None:
        self.items.append(ev)

    def has_ref(self, ref: str) -> bool:
        return any(i.vital.get("ref") == ref for i in self.items if i.kind == "git")


def simulate_417(
    line: str,
    *,
    log: Callable[[str], None] | None = None,
) -> str:
    """Record Ergo 417 without dropping queue responsibility (caller keeps queue)."""
    log = log or (lambda _m: None)
    preview = line[:80] if line else ""
    msg = f"ERROR 417 line too long preview={preview!r}"
    log(msg)
    return msg
