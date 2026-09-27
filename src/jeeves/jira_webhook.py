"""Inbound Jira webhook (/bob/v1/jira) — FR #190.

Customer POSTs Jira native payload (single ``issue`` or bulk ``issues``).
No outbound Jira API. Persist under digest home ``jira/``; announce on chair outbox.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

_SECRETISH = re.compile(
    r"(?i)(password\s*=\s*\S+|api[_-]?key\s*=\s*\S+|ghp_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9]{10,}|xox[baprs]-[A-Za-z0-9-]+|bearer\s+\S{8,})"
)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _redact(text: str) -> str:
    return _SECRETISH.sub("[redacted]", text or "")


def _adf_to_text(node: Any, *, limit: int = 4000) -> str:
    """Best-effort flatten of Atlassian Document Format or plain string."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node[:limit]
    if isinstance(node, (int, float, bool)):
        return str(node)[:limit]
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if len("".join(parts)) >= limit:
            return
        if isinstance(n, str):
            parts.append(n)
            return
        if isinstance(n, dict):
            if isinstance(n.get("text"), str):
                parts.append(n["text"])
            for ch in n.get("content") or []:
                walk(ch)
        elif isinstance(n, list):
            for ch in n:
                walk(ch)

    walk(node)
    return "".join(parts)[:limit]


def _field(fields: dict[str, Any], *path: str) -> Any:
    cur: Any = fields
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def extract_issue_record(
    issue: dict[str, Any],
    *,
    webhook_event: str = "",
    timestamp: Any = None,
    changelog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize one native Jira issue object. Unknown fields ignored."""
    if not isinstance(issue, dict):
        return None
    key = str(issue.get("key") or "").strip()
    if not key:
        return None
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    summary = str(fields.get("summary") or "").strip()
    desc_raw = fields.get("description")
    description = _adf_to_text(desc_raw) if not isinstance(desc_raw, str) else desc_raw
    status = _field(fields, "status", "name")
    assignee = _field(fields, "assignee", "displayName")
    project = _field(fields, "project", "key")
    items: list[dict[str, str]] = []
    cl = changelog if isinstance(changelog, dict) else None
    if cl is None and isinstance(issue.get("changelog"), dict):
        cl = issue.get("changelog")  # type: ignore[assignment]
    if isinstance(cl, dict):
        for it in cl.get("items") or []:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "field": str(it.get("field") or ""),
                    "from": str(it.get("fromString") or it.get("from") or ""),
                    "to": str(it.get("toString") or it.get("to") or ""),
                }
            )
    return {
        "key": key,
        "summary": summary,
        "description": description,
        "status": str(status or ""),
        "assignee": str(assignee or ""),
        "project": str(project or ""),
        "webhook_event": str(webhook_event or ""),
        "timestamp": timestamp,
        "changelog_items": items,
        "updated_at": _utc(),
    }


def iter_issues_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept single ``issue`` or bulk ``issues`` array (FR #190)."""
    event = str(payload.get("webhookEvent") or payload.get("webhook_event") or "")
    ts = payload.get("timestamp")
    changelog = payload.get("changelog") if isinstance(payload.get("changelog"), dict) else None
    out: list[dict[str, Any]] = []
    issues = payload.get("issues")
    if isinstance(issues, list):
        for iss in issues:
            if not isinstance(iss, dict):
                continue
            # per-issue changelog rare in bulk; use top-level if present
            rec = extract_issue_record(
                iss, webhook_event=event, timestamp=ts, changelog=changelog
            )
            if rec:
                out.append(rec)
        return out
    issue = payload.get("issue")
    if isinstance(issue, dict):
        rec = extract_issue_record(
            issue, webhook_event=event, timestamp=ts, changelog=changelog
        )
        if rec:
            out.append(rec)
    return out


def jira_store_dir(home: Path) -> Path:
    p = Path(home) / "jira"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_jira_tickets(home: Path) -> dict[str, Any]:
    path = jira_store_dir(home) / "tickets.json"
    if not path.is_file():
        return {"tickets": {}, "updated_at": ""}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tickets": {}, "updated_at": ""}
    if not isinstance(obj, dict):
        return {"tickets": {}, "updated_at": ""}
    tickets = obj.get("tickets")
    if not isinstance(tickets, dict):
        tickets = {}
    return {"tickets": tickets, "updated_at": str(obj.get("updated_at") or "")}


def save_jira_tickets(home: Path, doc: dict[str, Any]) -> Path:
    path = jira_store_dir(home) / "tickets.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def format_jira_announce(rec: dict[str, Any]) -> str:
    """Chair outbox line (vital fields first). No secrets."""
    event = str(rec.get("webhook_event") or "jira:issue_updated")
    # shorten jira:issue_created → issue_created
    short = event.replace("jira:", "") if event.startswith("jira:") else event
    key = str(rec.get("key") or "?")
    summary = _redact(str(rec.get("summary") or ""))[:120]
    status = str(rec.get("status") or "")
    assignee = str(rec.get("assignee") or "")
    parts = [f"JIRA {short} {key}"]
    if summary:
        parts.append(summary)
    if status:
        parts.append(f"status={status}")
    if assignee:
        parts.append(f"assignee={assignee}")
    return " ".join(parts)


def process_jira_webhook(
    payload: dict[str, Any],
    *,
    home: Path,
) -> tuple[list[str], list[dict[str, Any]], str | None]:
    """Apply Jira webhook. Returns (announce_lines, records, reject_reason)."""
    if not isinstance(payload, dict):
        return [], [], "invalid_json:not_object"
    records = iter_issues_from_payload(payload)
    if not records:
        # Valid JSON but nothing usable — soft success (no announce).
        return [], [], None
    doc = load_jira_tickets(home)
    tickets: dict[str, Any] = dict(doc.get("tickets") or {})
    lines: list[str] = []
    for rec in records:
        key = rec["key"]
        tickets[key] = rec
        lines.append(format_jira_announce(rec))
    save_jira_tickets(
        home,
        {"tickets": tickets, "updated_at": _utc(), "count": len(tickets)},
    )
    return lines, records, None
