"""Переключатели флагов zapret (GameFilter, IPSet) — как в меню Flowseal.

Состояние живёт в файлах внутри активной папки zapret (их читает service.bat при
запуске bat), поэтому после смены нужно перезапустить обход, чтобы winws перечитал.
Запись в Program Files требует прав администратора (они у приложения есть).
"""

import shutil
from pathlib import Path

from config import get_zapret_dir

DUMMY_IP = "203.0.113.113/32"   # «пустой» ipset (ничего не матчит)


# --------------------------------------------------------------- GameFilter
def _game_flag() -> Path:
    return get_zapret_dir() / "utils" / "game_filter.enabled"


def game_filter_mode() -> str:
    """'off' | 'all' | 'tcp' | 'udp'."""
    f = _game_flag()
    if not f.is_file():
        return "off"
    try:
        lines = [l.strip() for l in f.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    except OSError:
        return "off"
    m = lines[0].lower() if lines else ""
    return m if m in ("all", "tcp", "udp") else "off"


def set_game_filter(mode: str) -> bool:
    f = _game_flag()
    try:
        if mode == "off":
            if f.is_file():
                f.unlink()
            return True
        if mode in ("all", "tcp", "udp"):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes((mode + "\r\n").encode("ascii"))  # CRLF — как у Flowseal
            return True
    except OSError:
        pass
    return False


# --------------------------------------------------------------- IPSet
def _ipset() -> Path:
    return get_zapret_dir() / "lists" / "ipset-all.txt"


def _ipset_backup() -> Path:
    return get_zapret_dir() / "lists" / "ipset-all.txt.backup"


def ipset_loaded() -> bool:
    f = _ipset()
    if not f.is_file():
        return False
    try:
        lines = [l for l in f.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    except OSError:
        return False
    if not lines:
        return False
    if len(lines) == 1 and lines[0].strip() == DUMMY_IP:
        return False
    return True


def set_ipset(loaded: bool) -> bool:
    """loaded=True — восстановить реальный список из .backup; False — заглушка."""
    f = _ipset()
    try:
        if loaded:
            bak = _ipset_backup()
            if bak.is_file() and bak.stat().st_size > 50:
                shutil.copy2(bak, f)
                return True
            return False  # нет данных для загрузки (нужно «Обновить IPSet»)
        # выгрузка: сохраним текущий реальный список и поставим заглушку
        if ipset_loaded():
            try:
                shutil.copy2(f, _ipset_backup())
            except OSError:
                pass
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes((DUMMY_IP + "\r\n").encode("ascii"))
        return True
    except OSError:
        return False
