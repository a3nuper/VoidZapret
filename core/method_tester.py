"""Реальное тестирование стратегий zapret — поиск лучшего метода обхода.

Логика: для каждой .bat-стратегии запускается winws, затем выполняется
TLS-проверка соединения к набору обычно блокируемых хостов. Стратегия,
давшая больше всего успешных соединений (и меньший пинг), считается лучшей.
"""

import re
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import os

from config import get_zapret_dir, list_strategy_bats
from core.process_manager import kill_winws

# Хосты по умолчанию (если targets.txt не найден).
DEFAULT_HOSTS = [
    "discord.com",
    "gateway.discord.gg",
    "cdn.discordapp.com",
    "www.youtube.com",
    "i.ytimg.com",
    "www.google.com",
    "www.cloudflare.com",
]

# Цели для проверки конкретных сервисов (используются при подборе их стратегий).
DISCORD_HOSTS = [
    "discord.com",
    "gateway.discord.gg",
    "cdn.discordapp.com",
    "media.discordapp.net",
    "discordapp.com",
]

# Dead by Daylight работает поверх Epic Online Services — проверяем домены Epic.
DBD_HOSTS = [
    "epicgames.com",
    "www.epicgames.com",
    "store.epicgames.com",
    "api.epicgames.dev",
    "www.unrealengine.com",
]

# Совмещённый обход: стандартные DPI-блокируемые сайты (резолвятся по DNS,
# блокируются по SNI) — надёжный сигнал пробития для самопроверки.
COMBINED_HOSTS = [
    "www.youtube.com",
    "discord.com",
    "www.epicgames.com",
    "x.com",
]

INIT_WAIT_S = 3.5           # пауза на инициализацию winws после старта (драйвер WinDivert)
PROBE_TIMEOUT_S = 4.0       # таймаут одной TLS-проверки
PROBE_WORKERS = 16          # параллельных проверок (хосты проверяются разом)


@dataclass
class ConfigResult:
    """Результат тестирования одной стратегии."""

    name: str
    ok: int = 0
    total: int = 0
    elapsed: float = 0.0
    latency: float = 0.0  # средний пинг (мс) по успешным хостам, 0 если успехов нет
    failed: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.ok / self.total if self.total else 0.0


def _read_target_hosts() -> list[str]:
    """Достаёт https-хосты из zapret/utils/targets.txt, иначе дефолты."""
    targets_file = get_zapret_dir() / "utils" / "targets.txt"
    hosts: list[str] = []
    if targets_file.is_file():
        try:
            for line in targets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r'^\s*\w+\s*=\s*"https?://([^/"]+)', line)
                if m:
                    hosts.append(m.group(1))
        except OSError:
            pass
    # Убираем дубликаты, сохраняя порядок.
    seen: set[str] = set()
    unique = [h for h in hosts if not (h in seen or seen.add(h))]
    return unique or list(DEFAULT_HOSTS)


def _probe_host(host: str, timeout: float) -> tuple[bool, Optional[float]]:
    """Проверка пробития DPI по факту успешного TLS-рукопожатия.

    Возвращает (успех, пинг_мс). Критерий успеха — завершённое TLS-handshake с
    SNI=host: если DPI блокирует сайт, он сбрасывает (RST) или роняет ClientHello
    по SNI, и рукопожатие НЕ завершается. Если метод обхода пробил — handshake
    проходит. Раньше дополнительно слался HEAD и требовался ответ "HTTP", но
    многие хосты (websocket-gateway, API) не дают чистый HTTP-ответ на HEAD и
    давали ложные «не пробито» — поэтому теперь ориентируемся на само рукопожатие.
    Пинг — время connect + handshake (реальная задержка прохода через обход).
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    start = time.monotonic()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            raw.settimeout(timeout)
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                # Рукопожатие завершено — сервер прислал сертификат, DPI не сбросил.
                tls.getpeercert(binary_form=True)
                return True, (time.monotonic() - start) * 1000.0
    except (OSError, ssl.SSLError, socket.timeout):
        return False, None


def probe_hosts(
    hosts: list[str], timeout: float = PROBE_TIMEOUT_S, workers: int = PROBE_WORKERS,
) -> tuple[int, int, float]:
    """Проверяет связность до набора хостов. Возвращает (успешно, всего, ср_пинг_мс).

    Используется для самопроверки запущенного обхода (а не перебора стратегий).
    """
    if not hosts:
        return 0, 0, 0.0
    ok = 0
    latencies: list[float] = []
    # НЕ используем `with` — его выход ждёт зависшие потоки (например, повисший
    # DNS-резолв getaddrinfo, который не ограничен таймаутом сокета). Вместо этого
    # ограничиваем весь зонд общим дедлайном и бросаем зависшие потоки.
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts))))
    futures = [pool.submit(_probe_host, h, timeout) for h in hosts]
    deadline = time.monotonic() + timeout + 2
    for fut in futures:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            good, latency = fut.result(timeout=remaining)
        except Exception:
            good, latency = False, None
        if good:
            ok += 1
            if latency is not None:
                latencies.append(latency)
    pool.shutdown(wait=False)
    avg = sum(latencies) / len(latencies) if latencies else 0.0
    return ok, len(hosts), avg


class MethodTester:
    """Фоновый перебор стратегий с поиском лучшей."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._bats: Optional[list[Path]] = None
        self._hosts: Optional[list[str]] = None
        self._prefer_latency = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        on_log: Callable[[str], None],
        on_progress: Callable[[int, int, str], None],
        on_config_done: Callable[[ConfigResult], None],
        on_finished: Callable[[Optional[ConfigResult], list[ConfigResult]], None],
        *,
        bats: Optional[list[Path]] = None,
        hosts: Optional[list[str]] = None,
        prefer_latency: bool = False,
    ) -> bool:
        """Запускает тест в фоне. Возвращает False, если уже идёт.

        bats/hosts — явный список стратегий и целей (для подбора методов
        конкретного сервиса). Если не заданы — берутся general*.bat и targets.txt.
        prefer_latency — выбирать лучший метод по минимальному пингу, а не только
        по числу успешных хостов.
        """
        if self.is_running():
            return False
        self._cancel.clear()
        self._bats = bats
        self._hosts = hosts
        self._prefer_latency = prefer_latency
        self._thread = threading.Thread(
            target=self._run,
            args=(on_log, on_progress, on_config_done, on_finished),
            daemon=True,
        )
        self._thread.start()
        return True

    def _run(
        self,
        on_log: Callable[[str], None],
        on_progress: Callable[[int, int, str], None],
        on_config_done: Callable[[ConfigResult], None],
        on_finished: Callable[[Optional[ConfigResult], list[ConfigResult]], None],
    ) -> None:
        bats = self._bats if self._bats is not None else list_strategy_bats()
        zdir = get_zapret_dir()
        hosts = self._hosts if self._hosts is not None else _read_target_hosts()
        results: list[ConfigResult] = []

        if not bats:
            on_log("[!] Стратегии (.bat) не найдены в папке zapret")
            on_finished(None, results)
            return

        on_log(f"[i] Найдено стратегий: {len(bats)}, целей для проверки: {len(hosts)}")
        total = len(bats)

        try:
            for idx, bat in enumerate(bats, start=1):
                if self._cancel.is_set():
                    on_log("[i] Тест отменён пользователем")
                    break

                on_progress(idx, total, bat.name)
                on_log(f"[{idx}/{total}] Тестирую: {bat.name}")

                kill_winws()
                proc = self._start_strategy(bat, zdir)
                if proc is None:
                    on_log(f"    Не удалось запустить {bat.name}")
                    res = ConfigResult(name=bat.name, total=len(hosts))
                    results.append(res)
                    on_config_done(res)
                    continue

                # Ждём инициализации winws (с возможностью отмены).
                self._cancel.wait(INIT_WAIT_S)
                if self._cancel.is_set():
                    self._stop_strategy(proc)
                    break

                res = self._test_hosts(bat.name, hosts)
                results.append(res)
                ping = f"  ~{res.latency:.0f} мс" if res.ok else ""
                on_log(
                    f"    Успешно: {res.ok}/{res.total}{ping}"
                    + (f"  ✗ {', '.join(res.failed)}" if res.failed else "")
                )
                on_config_done(res)

                self._stop_strategy(proc)

                # Ранний выход: если стратегия пробила ВСЕ цели — она идеальна,
                # дальше перебирать смысла нет (если не оптимизируем по пингу).
                if not self._prefer_latency and res.total and res.ok == res.total:
                    on_log(f"[i] {bat.name} пробил все цели — останавливаю перебор")
                    break
        finally:
            kill_winws()

        best = self._pick_best(results, self._prefer_latency)
        if best:
            ping = f", ~{best.latency:.0f} мс" if best.latency else ""
            on_log(f"[✓] Лучший метод: {best.name} ({best.ok}/{best.total}{ping})")
        else:
            on_log("[!] Не удалось определить рабочий метод")
        on_finished(best, results)

    def _start_strategy(self, bat: Path, zdir: Path) -> Optional[subprocess.Popen]:
        env = os.environ.copy()
        env["NO_UPDATE_CHECK"] = "1"  # не открывать страницу релизов при тесте
        try:
            return subprocess.Popen(
                ["cmd.exe", "/c", str(bat)],
                cwd=str(zdir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, FileNotFoundError):
            return None

    def _stop_strategy(self, proc: subprocess.Popen) -> None:
        kill_winws()
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    def _test_hosts(self, name: str, hosts: list[str]) -> ConfigResult:
        res = ConfigResult(name=name, total=len(hosts))
        latencies: list[float] = []
        start = time.time()
        pool = ThreadPoolExecutor(max_workers=PROBE_WORKERS)
        futures = {pool.submit(_probe_host, h, PROBE_TIMEOUT_S): h for h in hosts}
        deadline = time.monotonic() + PROBE_TIMEOUT_S + 2
        for fut, host in futures.items():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                ok, latency = fut.result(timeout=remaining)
            except Exception:
                ok, latency = False, None
            if ok:
                res.ok += 1
                if latency is not None:
                    latencies.append(latency)
            else:
                res.failed.append(host)
        pool.shutdown(wait=False)
        res.elapsed = time.time() - start
        res.latency = sum(latencies) / len(latencies) if latencies else 0.0
        return res

    @staticmethod
    def _pick_best(
        results: list[ConfigResult], prefer_latency: bool = False,
    ) -> Optional[ConfigResult]:
        working = [r for r in results if r.ok > 0]
        if not working:
            return None
        if prefer_latency:
            # Сначала связность, затем минимальный пинг (меньше латентность — лучше).
            return max(working, key=lambda r: (r.ok, -r.latency))
        # Больше успешных хостов, при равенстве — быстрее общий прогон.
        return max(working, key=lambda r: (r.ok, -r.elapsed))
