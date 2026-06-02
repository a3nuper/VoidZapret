"""Управление winws через .bat (запуск, остановка, чтение вывода).

Важно: .bat запускает winws.exe НАПРЯМУЮ (без `start`), а сам cmd мы запускаем с
CREATE_NO_WINDOW. Так winws наследует скрытую консоль cmd — окна нет, и процесс
живёт как дочерний (вариант со `start /b` убивал winws при закрытии консоли).
Статус и остановку отслеживаем по процессам winws.exe (по PID).

Каждый ProcessManager отслеживает СВОИ процессы winws (по PID), а не все в
системе — это позволяет запускать несколько обходов (zapret + discord + dbd)
одновременно, не глуша друг друга.
"""

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Optional

WINWS = "winws.exe"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW
_SW_HIDE = 0

# Прячем консольные окна winws через WinAPI. winws запускается через `start /min`
# (это надёжно — у него своя консоль, процесс не умирает), а появившееся
# свёрнутое окно мы тут же скрываем, чтобы его не было видно вообще.
try:
    _user32 = ctypes.windll.user32
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
except (AttributeError, OSError):  # не Windows — заглушки
    _user32 = None
    _WNDENUMPROC = None


def hide_winws_windows() -> None:
    """Скрывает (SW_HIDE) все окна, принадлежащие процессам winws.exe."""
    if _user32 is None:
        return
    pids = winws_pids()
    if not pids:
        return

    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            _user32.ShowWindow(hwnd, _SW_HIDE)
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except OSError:
        pass

# Окно (сек), в течение которого после старта .bat ждём появления своего winws.
_CLAIM_WINDOW_S = 12.0

# Кэш снимка PID winws. Несколько ProcessManager (zapret + discord + dbd) опрашивают
# статус раз в секунду; без кэша каждый из них на UI-потоке запускает tasklist, что
# вызывает заметные подлагивания. Кэш с коротким TTL объединяет эти вызовы в один.
_PIDS_TTL_S = 0.7
_pids_lock = threading.Lock()
_pids_cache: set[int] = set()
_pids_cache_ts: float = 0.0


def _query_winws_pids() -> set[int]:
    """Реальный запрос tasklist (без кэша)."""
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {WINWS}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
        ).stdout
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == WINWS.lower():
                try:
                    pids.add(int(parts[1]))
                except ValueError:
                    pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    return pids


def _invalidate_pids_cache() -> None:
    """Сбрасывает кэш — следующий winws_pids() сделает свежий запрос."""
    global _pids_cache_ts
    with _pids_lock:
        _pids_cache_ts = 0.0


def _bat_env() -> dict:
    """Окружение для .bat: отключаем проверку обновлений zapret.

    service.bat при check_updates открывает страницу релизов в браузере, если
    вышла новая версия. NO_UPDATE_CHECK — штатный выход из этой логики.
    """
    env = os.environ.copy()
    env["NO_UPDATE_CHECK"] = "1"
    return env


def winws_pids(force: bool = False) -> set[int]:
    """Множество PID всех процессов winws.exe (кэшируется на _PIDS_TTL_S сек).

    force=True — игнорировать кэш и сделать свежий запрос (нужно при фиксации
    baseline перед запуском нового обхода).
    """
    global _pids_cache, _pids_cache_ts
    now = time.monotonic()
    if not force:
        with _pids_lock:
            if (now - _pids_cache_ts) < _PIDS_TTL_S:
                return set(_pids_cache)
    pids = _query_winws_pids()
    with _pids_lock:
        _pids_cache = set(pids)
        _pids_cache_ts = time.monotonic()
    return set(pids)


def winws_running() -> bool:
    """True, если хоть один процесс winws.exe присутствует в системе."""
    return bool(winws_pids())


def winws_pid() -> Optional[int]:
    """PID первого процесса winws.exe (или None)."""
    pids = winws_pids()
    return next(iter(pids)) if pids else None


def kill_pid(pid: int) -> None:
    """Завершает конкретный процесс по PID (вместе с дочерними)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    _invalidate_pids_cache()


def kill_winws() -> None:
    """Принудительно завершает ВСЕ процессы winws.exe (используется тестером)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", WINWS, "/T"],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    _invalidate_pids_cache()


# Службы драйвера WinDivert (winws/GoodbyeDPI/zapret регистрируют такие).
_WINDIVERT_SERVICES = ("windivert", "windivert14")


def reset_windivert() -> bool:
    """Сбрасывает битую/чужую службу WinDivert, чтобы winws пересоздал свою.

    Лечит ошибку «windivert: error opening filter: The service cannot be started,
    either because it is disabled or because it has no enabled devices…», которая
    остаётся после другого DPI-обхода (его служба WinDivert конфликтует/отключена).
    Требует прав администратора. Возвращает True, если что-то удалили.
    """
    changed = False
    kill_winws()  # драйвер не отпустится, пока есть процессы winws
    for svc in _WINDIVERT_SERVICES:
        try:
            subprocess.run(["sc", "stop", svc], capture_output=True,
                           timeout=10, creationflags=_NO_WINDOW)
            r = subprocess.run(["sc", "delete", svc], capture_output=True,
                               text=True, timeout=10, creationflags=_NO_WINDOW)
            if r.returncode == 0:
                changed = True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return changed


class ProcessManager:
    """Запуск .bat-стратегии с отслеживанием собственных процессов winws."""

    def __init__(self) -> None:
        self._cmd: Optional[subprocess.Popen] = None
        self._start_time: Optional[float] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._output_cb: Optional[Callable[[str], None]] = None
        self._stop_event = threading.Event()
        self._pids: set[int] = set()
        self._baseline: set[int] = set()
        self._claim_until: float = 0.0

    def start_bat(self, bat_path: Path, cwd: Path | None = None) -> bool:
        """Запускает .bat через cmd.exe. НЕ глушит чужие winws."""
        if not bat_path.is_file():
            return False

        # Запоминаем уже существующие winws — это чужие процессы.
        self._baseline = winws_pids(force=True)
        self._pids = set()
        self._claim_until = time.time() + _CLAIM_WINDOW_S

        try:
            work_dir = cwd or bat_path.parent
            self._stop_event.clear()
            self._cmd = subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(work_dir),
                env=_bat_env(),
                creationflags=_NO_WINDOW,
            )
            self._start_time = time.time()
            # cmd читаем всегда (вывод .bat при инициализации).
            self._output_cb = None
            self._start_reader()
            # Прячем свёрнутое окно winws сразу после появления (несколько попыток,
            # т.к. окно создаётся с небольшой задержкой после `start /min`).
            self._start_window_hider()
            return True
        except (OSError, FileNotFoundError):
            self._cmd = None
            return False

    def _start_reader(self) -> None:
        """Фоновое чтение stdout cmd/winws (всегда, чтобы не переполнялась труба).

        Вызывается только из start_bat — ровно один читатель на запуск. Читатели
        прошлых запусков завершаются сами по EOF своей (уже закрытой) трубы.
        """
        cmd = self._cmd

        def _reader() -> None:
            if not cmd or not cmd.stdout:
                return
            try:
                for line in cmd.stdout:  # итерация постоянно осушает трубу
                    if self._stop_event.is_set():
                        break
                    callback = self._output_cb
                    if callback:
                        callback(line.rstrip("\n"))
            except (ValueError, OSError):
                pass

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def _start_window_hider(self) -> None:
        """В течение ~6 сек прячет появляющиеся окна winws (без видимых консолей)."""
        def _hide_loop() -> None:
            for _ in range(15):
                if self._stop_event.is_set():
                    return
                hide_winws_windows()
                time.sleep(0.4)

        threading.Thread(target=_hide_loop, daemon=True).start()

    def _refresh_pids(self) -> None:
        """Захватывает появившиеся winws как свои и убирает завершённые."""
        current = winws_pids()
        # Пока открыто окно захвата и мы ещё не нашли свой winws — забираем новые.
        if not self._pids and time.time() < self._claim_until:
            new = current - self._baseline
            if new:
                self._pids = set(new)
        # Отбрасываем завершённые.
        self._pids &= current

    def stop(self) -> bool:
        """Завершает свои winws и сопутствующий cmd. True, если что-то остановили."""
        self._refresh_pids()
        was_running = bool(self._pids)
        self._stop_event.set()
        for pid in list(self._pids):
            kill_pid(pid)
        self._pids = set()
        self._claim_until = 0.0
        if self._cmd and self._cmd.poll() is None:
            try:
                self._cmd.terminate()
                self._cmd.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._cmd.kill()
                except OSError:
                    pass
        self._cmd = None
        self._start_time = None
        return was_running

    def is_running(self) -> bool:
        self._refresh_pids()
        return bool(self._pids)

    def get_pid(self) -> Optional[int]:
        self._refresh_pids()
        return next(iter(self._pids)) if self._pids else None

    def get_uptime(self) -> Optional[float]:
        if self._start_time and self.is_running():
            return time.time() - self._start_time
        return None

    def stream_output(self, callback: Callable[[str], None]) -> None:
        """Назначает обработчик вывода winws/cmd (читатель уже запущен в start_bat)."""
        self._output_cb = callback
