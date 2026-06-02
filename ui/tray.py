"""Иконка в системном трее — фоновая работа без окна.

При закрытии окна приложение прячется в трей (обход и watchdog продолжают
работать), а из меню трея можно вернуть окно или выйти полностью. Иконка
крутится в своём потоке pystray; колбэки меню маршалятся в Tk через .after().
"""

import threading
from pathlib import Path
from typing import Callable, Optional

try:
    import pystray
    from PIL import Image
    _AVAILABLE = True
except Exception:  # pystray/Pillow недоступны — трей просто отключён
    _AVAILABLE = False


class Tray:
    """Обёртка над pystray-иконкой с пунктами «Открыть» и «Выход»."""

    def __init__(
        self,
        icon_path: Optional[Path],
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._icon_path = icon_path
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def available() -> bool:
        return _AVAILABLE

    def is_active(self) -> bool:
        return self._icon is not None

    def _image(self):
        if self._icon_path and Path(self._icon_path).exists():
            try:
                return Image.open(self._icon_path)
            except Exception:
                pass
        # Запасная иконка — сплошной акцентный квадрат.
        return Image.new("RGB", (64, 64), (59, 110, 246))

    def start(self) -> None:
        """Показывает иконку в трее (если ещё не показана)."""
        if not _AVAILABLE or self._icon is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Открыть VoidZapret", self._show, default=True),
            pystray.MenuItem("Выход", self._quit),
        )
        self._icon = pystray.Icon("VoidZapret", self._image(), "VoidZapret", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _show(self) -> None:
        self.stop()
        self._on_show()

    def _quit(self) -> None:
        self.stop()
        self._on_quit()

    def stop(self) -> None:
        """Убирает иконку из трея."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
            self._thread = None
