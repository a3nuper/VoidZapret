"""QUIC-фикс для YouTube: блок исходящего UDP/443 правилом брандмауэра.

Многие сайты (особенно YouTube/googlevideo) гоняют видео по QUIC (UDP/443).
Если QUIC заблокировать, браузер откатывается на TCP/TLS — а его наш обход
надёжно пробивает. Правило брандмауэра действует сразу, переживает перезапуск,
легко снимается. Требует прав администратора.
"""

import subprocess

_NO_WINDOW = subprocess.CREATE_NO_WINDOW
_RULE = "VoidZapret-NoQUIC"


def quic_disabled() -> bool:
    """True, если правило-блокировка QUIC установлено."""
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={_RULE}"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_quic_disabled(on: bool) -> bool:
    try:
        # Снимаем прежнее правило в любом случае (идемпотентно).
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={_RULE}"],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW,
        )
        if on:
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={_RULE}", "dir=out", "action=block",
                 "protocol=UDP", "remoteport=443"],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW,
            )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
