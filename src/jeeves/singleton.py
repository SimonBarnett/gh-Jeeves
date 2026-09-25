"""Process singleton lock for BobJeeves (FR #39). No duplicate chair/receiver."""
from __future__ import annotations

import atexit
import json
import os
import time
from pathlib import Path


class SingletonError(RuntimeError):
    pass


def lock_path(home: Path, name: str = "bobjeeves") -> Path:
    return Path(home) / f".{name}.singleton.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                k.CloseHandle(h)
                return True
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def acquire(home: Path, *, name: str = "bobjeeves", role: str = "all") -> Path:
    """Acquire singleton lock under home. Raises SingletonError if another live holder."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = lock_path(home, name)
    my_pid = os.getpid()
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            ppid = int(prev.get("pid") or 0)
            if ppid != my_pid and _pid_alive(ppid):
                raise SingletonError(f"already running pid={ppid} role={prev.get('role')}")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    body = {
        "pid": my_pid,
        "role": role,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    def _release() -> None:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("pid") or 0) == my_pid:
                    path.unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_release)
    return path


def release(home: Path, *, name: str = "bobjeeves") -> None:
    path = lock_path(home, name)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("pid") or 0) == os.getpid():
                path.unlink(missing_ok=True)
    except Exception:
        pass
