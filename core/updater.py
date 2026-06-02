"""Обновление встроенного zapret из релизов Flowseal/zapret-discord-youtube.

Скачивает последний релиз (zip), распаковывает во внешнюю (постоянную) папку
zapret рядом с exe и сохраняет наши собственные стратегии (discord.bat, dbd.bat).
"""

import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from config import (
    CUSTOM_BAT_PREFIXES, get_bundled_zapret_dir, get_local_version,
    get_writable_zapret_dir,
)
from core.process_manager import kill_winws

REPO = "Flowseal/zapret-discord-youtube"
# Используем raw + codeload вместо api.github.com — у API строгий лимит запросов
# для неавторизованных вызовов (HTTP 403). Так делает и сам service.bat.
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/.service/version.txt"
ARCHIVE_URL = f"https://github.com/{REPO}/archive/refs/tags/{{tag}}.zip"
_UA = "VoidZapret-Updater"
_TIMEOUT = 20


class UpdateError(Exception):
    """Ошибка процесса обновления."""


def get_latest_release() -> tuple[str, str]:
    """Возвращает (tag, zip_url) последнего релиза. Бросает UpdateError при сбое."""
    req = urllib.request.Request(
        VERSION_URL, headers={"User-Agent": _UA, "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            tag = resp.read().decode("utf-8", errors="ignore").strip()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateError(f"Не удалось получить версию: {exc}") from exc

    if not tag:
        raise UpdateError("Пустой ответ при проверке версии")
    return tag, ARCHIVE_URL.format(tag=tag)


def is_update_available() -> tuple[bool, str, str]:
    """(есть_обновление, локальная_версия, последняя_версия). Не бросает."""
    local = get_local_version()
    try:
        tag, _ = get_latest_release()
    except UpdateError:
        return False, local, ""
    available = bool(tag) and tag.lstrip("v") != local.lstrip("v")
    return available, local, tag


def _download(url: str, dest: Path, on_progress: Optional[Callable[[float], None]]) -> None:
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
        raise UpdateError(f"Ошибка загрузки: {exc}") from exc


def _find_release_root(extract_dir: Path) -> Path:
    """Находит корень распакованного релиза (где лежит service.bat / bin)."""
    if (extract_dir / "service.bat").is_file() or (extract_dir / "bin").is_dir():
        return extract_dir
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    for sub in subdirs:
        if (sub / "service.bat").is_file() or (sub / "bin").is_dir():
            return sub
    return subdirs[0] if subdirs else extract_dir


def _preserve_custom_bats(target: Path) -> None:
    """Восстанавливает наши discord*/dbd*.bat из встроенной копии, если их нет в target.

    Релиз Flowseal не содержит этих файлов, поэтому при первой установке во внешнюю
    папку их нужно перенести из встроенной (bundled) копии.
    """
    bundled = get_bundled_zapret_dir()
    if not bundled or bundled == target:
        return
    for src in bundled.glob("*.bat"):
        if not src.name.lower().startswith(CUSTOM_BAT_PREFIXES):
            continue
        dst = target / src.name
        if not dst.is_file():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def download_and_install(
    on_log: Callable[[str], None],
    on_progress: Optional[Callable[[float], None]] = None,
) -> str:
    """Скачивает и устанавливает последний релиз. Возвращает установленный tag.

    Бросает UpdateError при любой проблеме.
    """
    on_log("Проверка последней версии...")
    tag, zip_url = get_latest_release()
    on_log(f"Последняя версия: {tag}")

    target = get_writable_zapret_dir()
    target.mkdir(parents=True, exist_ok=True)

    on_log("Останавливаю winws перед обновлением...")
    kill_winws()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "zapret.zip"

        on_log("Загрузка архива...")
        _download(zip_url, zip_path, on_progress)

        on_log("Распаковка...")
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            raise UpdateError(f"Архив повреждён: {exc}") from exc

        root = _find_release_root(extract_dir)
        on_log("Установка файлов...")
        try:
            shutil.copytree(root, target, dirs_exist_ok=True)
        except (OSError, shutil.Error) as exc:
            raise UpdateError(f"Не удалось скопировать файлы: {exc}") from exc

    _preserve_custom_bats(target)
    on_log(f"Готово. Установлена версия {tag}")
    return tag
