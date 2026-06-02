"""Оркестратор обхода — единый «мозг» поверх winws.

Получает список стратегий-кандидатов и набор целевых хостов. Логика:

 1. Перебирает кандидаты (предпочтительная — первой), поднимает winws, ждёт
    инициализацию и проверяет реальную связь до целей (самопроверка). Первая
    прошедшая стратегия становится активной.
 2. Watchdog: пока обход активен, периодически перепроверяет связность. Если
    winws умер или DPI «протух» (связь упала) — на лету переключается на другую
    рабочую стратегию (плохую отодвигает в конец очереди).

ВАЖНО про конкурентность: winws держит только один экземпляр, и _measure глушит
все winws перед стартом. Если бы два воркера крутились одновременно, они бы
глушили winws друг друга → каждая проверка давала бы мусор. Поэтому каждый запуск
помечается монотонной «эпохой»; при новом старте/стопе эпоха растёт, и устаревший
воркер немедленно выходит, ни разу больше не трогая winws. Это исключает гонку
двойного запуска (например, автоподключение + клик одновременно).

Все колбэки (on_state / on_log) вызываются из фоновых потоков — UI обязан
маршалить их в главный поток.
"""

import threading
from pathlib import Path
from typing import Callable, Optional

from config import get_zapret_dir
from core.method_tester import probe_hosts
from core.process_manager import (
    ProcessManager, kill_winws, reset_windivert, winws_running,
)

INIT_WAIT_S = 4.0          # пауза на инициализацию winws перед самопроверкой
SETTLE_WAIT_S = 0.6        # ждём освобождения драйвера WinDivert после kill
PROBE_TIMEOUT_S = 4.0      # таймаут одной TLS-проверки при самопроверке
WATCHDOG_PERIOD_S = 25.0   # как часто watchdog перепроверяет связность

ST_CONNECTING = "connecting"
ST_SWITCHING = "switching"
ST_RUNNING = "running"
ST_FAILED = "failed"
ST_STOPPED = "stopped"


class Orchestrator:
    """Единый управляющий слой обхода с самопроверкой и watchdog."""

    def __init__(
        self,
        on_state: Callable[[str, str], None],
        on_log: Callable[[str, str], None],
    ) -> None:
        self._on_state = on_state
        self._on_log = on_log
        self._pm = ProcessManager()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._epoch = 0                 # монотонная метка текущего запуска
        self._worker: Optional[threading.Thread] = None
        self._bats: list[Path] = []
        self._hosts: list[str] = []
        self._preferred_name = ""
        self._avoid_name = ""
        self._active_bat: Optional[Path] = None
        self._running = False
        self._fallback = False          # работаем на «лучшей доступной» (порог не взят)
        self._last_ok = 0
        self._last_total = 0
        self._last_lat = 0.0

    # ----------------------------------------------------------- свойства
    def is_running(self) -> bool:
        return self._running

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def active_bat(self) -> Optional[Path]:
        return self._active_bat

    def get_pid(self) -> Optional[int]:
        return self._pm.get_pid()

    def get_uptime(self) -> Optional[float]:
        return self._pm.get_uptime()

    def get_stats(self) -> tuple[int, int, float]:
        return self._last_ok, self._last_total, self._last_lat

    # --------------------------------------------------------- управление
    def start(self, bats: list[Path], hosts: list[str], preferred_name: str = "") -> None:
        """Запускает обход: bats — кандидаты, hosts — цели самопроверки."""
        self._bats = list(bats)
        self._hosts = list(hosts)
        self._preferred_name = preferred_name
        self._avoid_name = ""
        self._running = False
        self._active_bat = None
        self._launch()

    def stop(self) -> None:
        """Останавливает обход и watchdog, глушит свой winws."""
        with self._lock:
            self._epoch += 1            # любая текущая работа становится устаревшей
            self._cancel.set()
            self._pm.stop()
            self._running = False
            self._active_bat = None

    def _launch(self) -> None:
        """Стартует новый воркер с новой эпохой (старый сам выйдет)."""
        with self._lock:
            self._epoch += 1
            epoch = self._epoch
            self._cancel.set()          # прерываем ожидания старого воркера
            self._pm.stop()
            self._cancel.clear()
            self._worker = threading.Thread(
                target=self._connect_worker, args=(epoch,), daemon=True)
            self._worker.start()

    def _alive(self, epoch: int) -> bool:
        """True, пока этот воркер актуален (эпоха не сменилась)."""
        return epoch == self._epoch

    # ------------------------------------------------------------ подбор
    def _candidate_order(self) -> list[Path]:
        if not self._bats:
            return []
        order = list(self._bats)
        if self._avoid_name:
            order.sort(key=lambda b: b.name == self._avoid_name)
        pref = next((b for b in order if b.name == self._preferred_name), None)
        if pref is not None and pref.name != self._avoid_name:
            order = [pref] + [b for b in order if b != pref]
        return order

    def _measure(self, bat: Path, epoch: int) -> Optional[tuple[int, int, float]]:
        """Глушит winws, запускает bat, ждёт init, проверяет связь.

        Перед каждым касанием winws проверяет эпоху — устаревший воркер не тронет
        winws нового воркера (исключает взаимное глушение).
        """
        if not self._alive(epoch):
            return None
        self._pm.stop()
        kill_winws()
        if self._cancel.wait(SETTLE_WAIT_S) or not self._alive(epoch):
            return None
        zdir = get_zapret_dir()
        cwd = zdir if zdir.is_dir() else None
        if not self._pm.start_bat(bat, cwd=cwd):
            self._on_log(f"[!] {bat.name}: не удалось запустить cmd/winws", "error")
            return None
        # Диагностика: показываем вывод самого winws/cmd в журнал.
        self._pm.stream_output(lambda ln: self._on_log(ln, "output"))
        if self._cancel.wait(INIT_WAIT_S) or not self._alive(epoch):
            return None
        # Диагностика: жив ли winws к моменту проверки.
        if not winws_running():
            self._on_log(f"[!] {bat.name}: winws НЕ запущен (сразу вышел — "
                         f"драйвер WinDivert/права?)", "error")
        return probe_hosts(self._hosts, timeout=PROBE_TIMEOUT_S)

    @staticmethod
    def _passed(ok: int, total: int) -> bool:
        return ok >= max(1, (total + 1) // 2)

    def _start_only(self, bat: Path, epoch: int) -> bool:
        """Поднимает bat без самопроверки (для фолбэка «оставить лучшую»)."""
        if not self._alive(epoch):
            return False
        self._pm.stop()
        kill_winws()
        if self._cancel.wait(SETTLE_WAIT_S) or not self._alive(epoch):
            return False
        zdir = get_zapret_dir()
        cwd = zdir if zdir.is_dir() else None
        return self._pm.start_bat(bat, cwd=cwd)

    def _connect_worker(self, epoch: int) -> None:
        order = self._candidate_order()
        if not order:
            if self._alive(epoch):
                self._running = False
                self._on_state(ST_FAILED, "Стратегии не найдены в папке zapret")
            return

        best: Optional[tuple[Path, int, int, float]] = None
        healed = False
        for idx, bat in enumerate(order, start=1):
            if not self._alive(epoch):
                return
            self._on_state(ST_CONNECTING, f"Проверка стратегии {idx} из {len(order)}…")
            self._on_log(f"Пробую {bat.name} ({idx}/{len(order)})…", "system")
            res = self._measure(bat, epoch)
            if not self._alive(epoch):
                return
            # Самолечение: winws не встал из-за битой службы WinDivert — один раз
            # сбрасываем службу и повторяем эту же стратегию.
            if not healed and not winws_running():
                healed = True
                self._on_log("Сбрасываю службу WinDivert и пробую снова…", "system")
                reset_windivert()
                if self._cancel.wait(SETTLE_WAIT_S) or not self._alive(epoch):
                    return
                res = self._measure(bat, epoch)
                if not self._alive(epoch):
                    return
            if res is None:
                continue
            ok, total, lat = res
            if best is None or ok > best[1]:
                best = (bat, ok, total, lat)
            if self._passed(ok, total):
                if not self._alive(epoch):
                    return
                self._fallback = False
                self._active_bat = bat
                self._running = True
                self._last_ok, self._last_total, self._last_lat = ok, total, lat
                ping = f", ~{lat:.0f} мс" if lat else ""
                self._on_log(f"✓ Работает: {bat.name} (связь {ok}/{total}{ping})", "ok")
                self._on_state(ST_RUNNING, self._running_detail(bat, ok, total, lat))
                self._start_watchdog(epoch)
                return
            self._on_log(f"{bat.name}: связь {ok}/{total} — мало, пробую дальше",
                         "system")

        if not self._alive(epoch):
            return

        # Ни одна не взяла порог. Как в v1 — НЕ глушим winws, а оставляем работать
        # лучшую из проверенных (обход всё равно активен; самопроверка может быть
        # пессимистичной из-за DNS/таймингов, а реальный трафик пробивается).
        if best is not None and self._start_only(best[0], epoch):
            self._cancel.wait(INIT_WAIT_S)
            if not self._alive(epoch):
                return
            bat, ok, total, lat = best
            self._fallback = True
            self._active_bat = bat
            self._running = True
            self._last_ok, self._last_total, self._last_lat = ok, total, lat
            self._on_log(
                f"Порог не взят — оставляю лучшую: {bat.name} ({ok}/{total}). "
                f"Обход активен; если сайты не открываются — попробуй другой DNS "
                f"или «Подобрать лучший метод».", "system")
            self._on_state(ST_RUNNING, self._running_detail(bat, ok, total, lat) + "  ·  ограниченно")
            self._start_watchdog(epoch)
            return

        self._pm.stop()
        self._running = False
        self._on_state(ST_FAILED, "Не удалось запустить обход (нужны права администратора?)")

    @staticmethod
    def _running_detail(bat: Path, ok: int, total: int, lat: float) -> str:
        ping = f"  ·  ~{lat:.0f} мс" if lat else ""
        return f"{bat.stem}  ·  связь {ok}/{total}{ping}"

    # ---------------------------------------------------------- watchdog
    def _start_watchdog(self, epoch: int) -> None:
        threading.Thread(target=self._watchdog, args=(epoch,), daemon=True).start()

    def _watchdog(self, epoch: int) -> None:
        while self._alive(epoch):
            if self._cancel.wait(WATCHDOG_PERIOD_S) or not self._alive(epoch):
                return
            if not self._running:
                return
            if not self._pm.is_running():
                self._on_log("winws неожиданно завершился — переподбор", "error")
                self._recover(epoch, self._active_bat)
                return
            # В режиме фолбэка («лучшая доступная») не переподбираем из-за низкой
            # связи — мы уже знаем, что порог не взят, и сознательно держим winws.
            if self._fallback:
                continue
            ok, total, _ = probe_hosts(self._hosts, timeout=PROBE_TIMEOUT_S)
            if not self._alive(epoch):
                return
            if not self._passed(ok, total):
                self._on_log(
                    f"Связь просела ({ok}/{total}) — переключаю стратегию", "system")
                self._recover(epoch, self._active_bat)
                return

    def _recover(self, epoch: int, bad_bat: Optional[Path]) -> None:
        """Перезапускает подбор (новая эпоха), отодвигая «плохую» стратегию."""
        if not self._alive(epoch):
            return
        self._avoid_name = bad_bat.name if bad_bat else ""
        self._running = False
        self._on_state(ST_SWITCHING, "Переключение стратегии…")
        self._launch()
