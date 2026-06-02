"""Управление Cloudflare WARP через warp-cli (для UI-моста)."""

import subprocess

_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def status() -> tuple[bool, str]:
    """(подключён?, текст статуса). warp-cli не найден → (False, сообщение)."""
    try:
        res = subprocess.run(
            ["warp-cli", "status"], capture_output=True, text=True,
            timeout=5, creationflags=_NO_WINDOW,
        )
        out = res.stdout.strip()
        connected = "Connected" in out or "Connecting" in out
        return connected, out or "—"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, "warp-cli не найден"


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                       creationflags=_NO_WINDOW)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


def connect() -> None:
    _run(["warp-cli", "connect"])


def disconnect() -> None:
    _run(["warp-cli", "disconnect"])
