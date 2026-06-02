"""Точка входа VoidZapret — WebView-приложение (pywebview + WebView2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.admin import is_admin, relaunch_as_admin
from webapp import run


def main() -> None:
    # Поднимаем права один раз — иначе каждый запуск winws вызывает UAC.
    if not is_admin():
        if relaunch_as_admin():
            return  # повышенный экземпляр запущен, текущий завершаем
    run()


if __name__ == "__main__":
    main()
