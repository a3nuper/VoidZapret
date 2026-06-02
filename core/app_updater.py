"""Авто-обновление самого приложения VoidZapret через GitHub Releases.

Сравнивает APP_VERSION с тегом последнего релиза. При наличии новой версии
скачивает ассет-установщик (VoidZapret-Setup-*.exe) и запускает его — он ставит
новую версию поверх. Конфиг пользователя установщик сохраняет.

ВАЖНО: задай GITHUB_REPO = "owner/repo" после публикации репозитория.
"""

import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from config import APP_VERSION

# TODO: указать после публикации (например, "a3nuper/VoidZapret").
GITHUB_REPO = "a3nuper/VoidZapret"

_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_UA = "VoidZapret-AppUpdater"
_TIMEOUT = 20


class AppUpdateError(Exception):
    pass


def _norm(v: str) -> tuple:
    """Версия → кортеж чисел для сравнения ('v3.1' → (3,1))."""
    nums = []
    for part in v.lstrip("vV").replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple(nums) or (0,)


def get_latest() -> tuple[str, Optional[str]]:
    """(тег, url_установщика). Бросает AppUpdateError при сбое."""
    req = urllib.request.Request(_API, headers={"User-Agent": _UA,
                                                "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        raise AppUpdateError(f"Не удалось проверить версию: {exc}") from exc

    tag = (data.get("tag_name") or "").strip()
    if not tag:
        raise AppUpdateError("Релизы не найдены")
    setup_url = None
    for asset in data.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe") and "setup" in name:
            setup_url = asset.get("browser_download_url")
            break
    return tag, setup_url


def is_update_available() -> tuple[bool, str, str]:
    """(есть_обновление, текущая, последняя). Не бросает."""
    try:
        tag, _ = get_latest()
    except AppUpdateError:
        return False, APP_VERSION, ""
    return _norm(tag) > _norm(APP_VERSION), APP_VERSION, tag


def download_installer(url: str, on_progress: Optional[Callable[[float], None]] = None) -> Path:
    """Скачивает установщик во временный файл, возвращает путь."""
    if not url:
        raise AppUpdateError("В релизе нет установщика (.exe)")
    dest = Path(tempfile.gettempdir()) / "VoidZapret-Setup-latest.exe"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            read = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read += len(chunk)
                    if on_progress and total:
                        on_progress(min(read / total, 1.0))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise AppUpdateError(f"Ошибка загрузки: {exc}") from exc
    return dest


def run_installer(path: Path) -> None:
    """Запускает установщик (тихо обновит поверх) и оставляет его работать."""
    import subprocess
    subprocess.Popen([str(path), "/SILENT"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
