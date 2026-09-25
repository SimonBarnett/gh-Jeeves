"""K5 / FR #6: running version identity, release tags, drift check.

Deploy from a **tagged** gh-Jeeves release (not a detached hotpatch worktree).
Running version is reported on the digest webhook for TipForm / health drift.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__ as PKG_VERSION

# Release tags: v0.2.0 or 0.2.0
_TAG_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z]+)?)$")


@dataclass(frozen=True)
class VersionInfo:
    """What this process believes it is running."""

    package: str
    release_tag: str  # canonical vX.Y.Z when known
    git_describe: str
    source: str  # package | VERSION | git | env
    tree_dirty: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = bool(self.release_tag or self.package)
        return d

    @property
    def report_string(self) -> str:
        """Compact string for webhook / !status."""
        tag = self.release_tag or f"v{self.package}"
        if self.git_describe and self.git_describe not in (tag, self.package):
            return f"{tag} ({self.git_describe})"
        return tag


def normalize_tag(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = _TAG_RE.match(s)
    if not m:
        # allow describe-like v0.2.0-3-gabc
        if s.startswith("v") or s[0].isdigit():
            return s if s.startswith("v") else f"v{s}"
        return s
    return f"v{m.group(1)}"


def read_version_file(root: Path | None = None) -> str:
    """VERSION file written at release packaging time (preferred over dirty git)."""
    root = root or Path(__file__).resolve().parents[2]
    for name in ("VERSION", "version.txt"):
        p = root / name
        if p.is_file():
            return normalize_tag(p.read_text(encoding="utf-8").splitlines()[0] if p.stat().st_size else "")
    return ""


def git_describe(root: Path | None = None) -> tuple[str, bool]:
    """Return (describe, dirty). Empty describe if not a git checkout."""
    root = root or Path(__file__).resolve().parents[2]
    try:
        desc = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if desc.returncode != 0:
            return "", False
        out = (desc.stdout or "").strip()
        dirty = out.endswith("-dirty")
        return out, dirty
    except (OSError, subprocess.SubprocessError):
        return "", False


def running_version(root: Path | None = None) -> VersionInfo:
    """
    Resolve running identity.

    Priority: JEEVES_RELEASE_TAG env → VERSION file → package __version__ + git describe.
    """
    root = root or Path(__file__).resolve().parents[2]
    env_tag = normalize_tag(os.environ.get("JEEVES_RELEASE_TAG") or "")
    if env_tag:
        desc, dirty = git_describe(root)
        return VersionInfo(
            package=PKG_VERSION,
            release_tag=env_tag,
            git_describe=desc,
            source="env",
            tree_dirty=dirty,
        )
    file_tag = read_version_file(root)
    desc, dirty = git_describe(root)
    if file_tag:
        return VersionInfo(
            package=PKG_VERSION,
            release_tag=file_tag,
            git_describe=desc or file_tag,
            source="VERSION",
            tree_dirty=dirty,
        )
    # package + optional git
    tag = normalize_tag(PKG_VERSION)
    return VersionInfo(
        package=PKG_VERSION,
        release_tag=tag,
        git_describe=desc or tag,
        source="package" if not desc else "git",
        tree_dirty=dirty,
    )


def expected_release_tag(root: Path | None = None, pinned: str | None = None) -> str:
    """Tag operators expect (pin file, env, or latest VERSION)."""
    if pinned:
        return normalize_tag(pinned)
    env = normalize_tag(os.environ.get("JEEVES_EXPECTED_TAG") or "")
    if env:
        return env
    root = root or Path(__file__).resolve().parents[2]
    pin = root / "EXPECTED_RELEASE"
    if pin.is_file():
        return normalize_tag(pin.read_text(encoding="utf-8").splitlines()[0])
    return read_version_file(root) or normalize_tag(PKG_VERSION)


@dataclass(frozen=True)
class DriftResult:
    ok: bool
    running: str
    expected: str
    drifted: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_drift(
    *,
    root: Path | None = None,
    running: VersionInfo | None = None,
    expected: str | None = None,
    allow_dirty: bool = False,
) -> DriftResult:
    """
    Drift when running release_tag != expected, or tree is dirty when disallowed.

    Detached hotpatch / wrong commit: git_describe not equal to expected tag
    and not a clean exact tag match.
    """
    root = root or Path(__file__).resolve().parents[2]
    run = running or running_version(root)
    exp = normalize_tag(expected or expected_release_tag(root))
    run_tag = normalize_tag(run.release_tag or run.package)

    if not exp:
        return DriftResult(
            ok=False,
            running=run.report_string,
            expected="",
            drifted=True,
            reason="no_expected_tag",
        )

    if run.tree_dirty and not allow_dirty:
        return DriftResult(
            ok=False,
            running=run.report_string,
            expected=exp,
            drifted=True,
            reason="dirty_tree",
        )

    # Exact tag match
    if run_tag == exp:
        # if describe shows commits after tag, drifted
        desc = run.git_describe or ""
        if desc and desc != exp and re.search(r"-\d+-g[0-9a-f]+", desc):
            return DriftResult(
                ok=False,
                running=run.report_string,
                expected=exp,
                drifted=True,
                reason="commits_after_tag",
            )
        return DriftResult(
            ok=True,
            running=run.report_string,
            expected=exp,
            drifted=False,
            reason="match",
        )

    return DriftResult(
        ok=False,
        running=run.report_string,
        expected=exp,
        drifted=True,
        reason="tag_mismatch",
    )


def version_report_payload(
    root: Path | None = None,
    *,
    include_drift: bool = True,
    allow_dirty: bool | None = None,
) -> dict[str, Any]:
    """Shape posted to digest webhook / embedded in GET report."""
    run = running_version(root)
    payload: dict[str, Any] = {
        "jeeves_version": run.as_dict(),
        "version": run.report_string,
    }
    if include_drift:
        # Default: allow dirty working trees in dev/CI; production sets JEEVES_REQUIRE_CLEAN=1
        if allow_dirty is None:
            allow_dirty = os.environ.get("JEEVES_REQUIRE_CLEAN", "").strip() not in (
                "1",
                "true",
                "yes",
            )
        drift = check_drift(root=root, running=run, allow_dirty=bool(allow_dirty))
        payload["version_drift"] = drift.as_dict()
    return payload


def write_version_stamp(home: Path, root: Path | None = None) -> Path:
    """Persist version JSON under digest/jeeves home for crash inspection."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = home / "jeeves_version.json"
    path.write_text(json.dumps(version_report_payload(root), indent=2) + "\n", encoding="utf-8")
    return path
