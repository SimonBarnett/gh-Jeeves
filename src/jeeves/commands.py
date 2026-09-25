"""Jeeves command registry — single source for dispatcher and !help (FR #27).

Scripts only. Help text is generated from this registry so it cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Roles: who may see/use the command in !help and (later) dispatch.
ROLE_WORKER = "worker"
ROLE_BOB = "bob"
ROLE_SIMON = "simon"
ALL_ROLES = frozenset({ROLE_WORKER, ROLE_BOB, ROLE_SIMON})


@dataclass(frozen=True)
class CommandSpec:
    name: str  # without leading !
    syntax: str  # e.g. !list [all|<repo>]
    summary: str
    roles: frozenset[str]
    example: str = ""
    details: str = ""
    related: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("command name required")
        if not (self.syntax or "").strip():
            raise ValueError(f"{self.name}: syntax required")
        if not (self.summary or "").strip():
            raise ValueError(f"{self.name}: summary required")
        if not self.roles:
            raise ValueError(f"{self.name}: roles required")


def _reg() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec(
            name="help",
            syntax="!help [cmd]",
            summary="list commands or detail one command (PM only)",
            roles=ALL_ROLES,
            example="!help list",
            details="Replies by private message only. Bare !help is one line per visible command. !help <cmd> is at most 5 lines.",
            related=("list", "status"),
        ),
        CommandSpec(
            name="list",
            syntax="!list [all|<repo>|fr|mrb|uat]",
            summary="queue by PM: one line per job (type in channel; reply is PM)",
            roles=ALL_ROLES,
            example="!list SimonBarnett/gh-Jeeves",
            details=(
                "In-channel or PM. Format: FR owner/repo#n title. "
                "!list all includes accepted; !list <repo> filters. "
                "Paced PM lines under 400 bytes; truncated lists end with +M more."
            ),
            related=("help", "status", "resync"),
        ),
        CommandSpec(
            name="status",
            syntax="!status",
            summary="Jeeves version, uptime, queue counts, last queue rebuild time, busy/idle workers",
            roles=ALL_ROLES,
            example="!status",
            details="Read-only snapshot from queue.json / digest home.",
            related=("list", "resync", "help"),
        ),
        CommandSpec(
            name="resync",
            syntax="!resync",
            summary="rebuild the queue from GitHub now",
            roles=frozenset({ROLE_BOB, ROLE_SIMON}),
            example="!resync",
            details="Restricted to simon and bob-* nicks. Quiet when unchanged. See FR #25.",
            related=("list", "status"),
        ),
        CommandSpec(
            name="sweep",
            syntax="!sweep [channel]",
            summary="re-apply +h/+o grants in a channel (simon only)",
            roles=frozenset({ROLE_SIMON}),
            example="!sweep #bobiverse",
            details=(
                "FR #52: Jeeves auto-grants +h to authenticated bob-* and +o to "
                "authenticated simon from a fleet host. !sweep re-runs grants; no channel text."
            ),
            related=("help", "status"),
        ),
        CommandSpec(
            name="ignore",
            syntax="!ignore {repo}",
            summary="suppress a repo from the whole Jeeves process (simon/ops)",
            roles=frozenset({ROLE_BOB, ROLE_SIMON}),
            example="!ignore SimonBarnett/old-sandbox",
            details=(
                "FR #75: adds owner/name or bare name to ignored.json (alongside queue.json). "
                "Ignored repos get no #bobiverse announce, no queue/digest enqueue, no !list "
                "rows, no ear offers, no supersede. Also purges already-queued items for that repo. "
                "Works token-less. Interacts with !focus: ignored never appears regardless of priority."
            ),
            related=("ignored", "unignore", "list"),
        ),
        CommandSpec(
            name="ignored",
            syntax="!ignored",
            summary="list ignored repos by PM (open to anyone)",
            roles=ALL_ROLES,
            example="!ignored",
            details="FR #75: replies by PM with the current ignore list (or ignored: (none)).",
            related=("ignore", "unignore", "list"),
        ),
        CommandSpec(
            name="unignore",
            syntax="!unignore {repo}",
            summary="resume handling a previously ignored repo (simon/ops)",
            roles=frozenset({ROLE_BOB, ROLE_SIMON}),
            example="!unignore old-sandbox",
            details=(
                "FR #75: removes the repo from ignored.json. New events are handled again; "
                "GitHub resync may re-add open items. Does not rebuild history by itself."
            ),
            related=("ignore", "ignored", "resync"),
        ),
        CommandSpec(
            name="focus",
            syntax="!focus [n|high|medium|low] {repo}",
            summary="priority-sort !list and !bored offers (simon account)",
            roles=frozenset({ROLE_SIMON}),
            example="!focus high SimonBarnett/gh-Jeeves",
            details=(
                "FR #68: persists focus.json beside queue.json. Lower number first "
                "(high=1, medium=5, low=9). Bare !focus lists; bare !focus {repo} = high. "
                "Same sort for !list and ear top_unaccepted. Ignored repos stay hidden. "
                "PM only. Simon services account (like !sweep)."
            ),
            related=("unfocus", "list"),
        ),
        CommandSpec(
            name="unfocus",
            syntax="!unfocus {repo}|all",
            summary="remove a repo from focus or clear all (simon account)",
            roles=frozenset({ROLE_SIMON}),
            example="!unfocus all",
            details="FR #68: drops focus entries; queue order returns to seq-only for those repos.",
            related=("focus", "list"),
        ),
    )


# Module-level registry (immutable tuple)
COMMANDS: tuple[CommandSpec, ...] = _reg()
COMMAND_BY_NAME: dict[str, CommandSpec] = {c.name.lower(): c for c in COMMANDS}


def all_commands() -> tuple[CommandSpec, ...]:
    return COMMANDS


def get_command(name: str) -> CommandSpec | None:
    return COMMAND_BY_NAME.get((name or "").strip().lstrip("!").lower())


def commands_for_roles(roles: Iterable[str]) -> list[CommandSpec]:
    want = {r.lower() for r in roles}
    return [c for c in COMMANDS if c.roles & want]


def validate_registry() -> list[str]:
    """Return problems (empty if every command has syntax+summary)."""
    errs: list[str] = []
    seen: set[str] = set()
    for c in COMMANDS:
        key = c.name.lower()
        if key in seen:
            errs.append(f"duplicate:{key}")
        seen.add(key)
        if not c.syntax.strip():
            errs.append(f"no_syntax:{key}")
        if not c.summary.strip():
            errs.append(f"no_summary:{key}")
        if not c.roles:
            errs.append(f"no_roles:{key}")
    return errs


def readme_command_table() -> str:
    """Markdown table generated from registry (for docs/README)."""
    lines = [
        "| Command | Syntax | Who | Summary |",
        "|---------|--------|-----|---------|",
    ]
    for c in COMMANDS:
        who = ", ".join(sorted(c.roles))
        lines.append(f"| `!{c.name}` | `{c.syntax}` | {who} | {c.summary} |")
    lines.append("")
    lines.append(
        "Worker shop lines (`ACK`/`DONE`/`!bored`) and ear `OFFER` are not Jeeves PM commands; see README shop grammar."
    )
    return "\n".join(lines)
