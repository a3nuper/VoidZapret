"""Автозапуск VoidZapret при старте Windows (ключ реестра HKCU...\\Run)."""

import sys
from pathlib import Path

_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "VoidZapret"


def _exe_command() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable))
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{main_py}"'


def is_enabled() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, _VALUE)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Включает/выключает автозапуск. True — успех."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, _exe_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE)
                except FileNotFoundError:
                    pass
            return True
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False
