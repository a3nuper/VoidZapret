"""Проверка и однократное повышение прав до администратора.

winws.exe (драйвер WinDivert) требует прав администратора. Если приложение
запущено без них, каждый запуск winws вызывает отдельный UAC-запрос. Поднимая
права один раз при старте, все дочерние процессы наследуют админ-токен.
"""

import ctypes
import os
import subprocess
import sys


def is_admin() -> bool:
    """True, если процесс запущен с правами администратора."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    """Перезапускает приложение с повышенными правами (UAC один раз).

    Возвращает True, если запрос на повышение отправлен (текущий процесс
    должен завершиться). False — если повысить не удалось.
    """
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            params = subprocess.list2cmdline([script, *sys.argv[1:]])

        # ShellExecuteW с глаголом "runas" вызывает UAC и наследует права.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, None, 1
        )
        return rc > 32
    except (AttributeError, OSError):
        return False
