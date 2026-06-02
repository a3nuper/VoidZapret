"""VoidZapret — приложение на WebView (pywebview + WebView2).

Полный перенос UI на HTML/CSS/JS. Питон-ядро (оркестратор, тестер, апдейтер,
warp, автозапуск) переиспользуется как есть; общение с фронтом — через мост
js_api: JS зовёт публичные методы Api (pull), Python шлёт события в JS через
evaluate_js (push).
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import webview

from config import (
    APP_VERSION, Config, get_icon_path, get_local_version, list_service_bats,
    list_strategy_bats,
)
from core import app_updater, autostart, dns, quic, updater, warp, zapret_flags
from core.admin import is_admin
from config import get_winws_path
from core.method_tester import MethodTester, COMBINED_HOSTS, _probe_host, probe_hosts
from core.orchestrator import Orchestrator, ST_FAILED, ST_RUNNING
from core.process_manager import kill_winws, reset_windivert
from core.engine.engine import VoidEngine
from core.engine import strategies as engine_strategies
from ui.tray import Tray


def _webui_path() -> str:
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).parent
    return str(root / "webui" / "index.html")


# Сервисы для попапа «Соединение» (имя → хост проверки).
SERVICES = [
    ("YouTube", "www.youtube.com"),
    ("Discord", "discord.com"),
    ("Epic / Игры", "www.epicgames.com"),
    ("X / Twitter", "x.com"),
]

# Хост для живого пинга (TCP-connect).
_PING_HOST = "www.youtube.com"


def _tcp_ping(host: str = _PING_HOST, port: int = 443, timeout: float = 2.0):
    """Время TCP-коннекта в мс (или None при ошибке)."""
    import socket
    import time as _t
    start = _t.monotonic()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return (_t.monotonic() - start) * 1000.0
    except OSError:
        return None


class Api:
    """Мост JS ↔ Python."""

    def __init__(self) -> None:
        self._config = Config.load()
        self._window = None
        self._want = False
        self._updating = False
        self._eval_lock = threading.Lock()  # сериализует evaluate_js (иначе дедлок)
        self._ping_on = False
        # Наш собственный движок (приоритетный метод).
        self._engine = VoidEngine(on_log=lambda m: self._push("onLog", m, "output"))
        self._engine_on = False
        self._engine_strategy = ""
        self._engine_stats = (0, 0, 0.0)
        self._engine_start = 0.0
        self._start_epoch = 0     # инвалидация устаревшего _smart_start
        self._orch = Orchestrator(on_state=self._on_state, on_log=self._on_log)
        self._tester = MethodTester()
        self._tray = Tray(
            icon_path=get_icon_path(),
            on_show=self._restore_from_tray,
            on_quit=self._quit,
            on_toggle=lambda: self.toggle(),
            is_running=lambda: self._want,
        )
        threading.Thread(target=self._stats_loop, daemon=True).start()

    def bind(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------- helpers
    def _eval(self, js: str) -> None:
        if self._window is None:
            return
        # ВАЖНО: одновременные evaluate_js из разных потоков (воркер оркестратора,
        # stats-loop, поток toggle) дедлочат мост WebView2 — пуш зависает, перебор
        # «застывает» на первой стратегии. Лок гарантирует строго один вызов за раз.
        with self._eval_lock:
            try:
                self._window.evaluate_js(js)
            except Exception:
                pass

    def _push(self, fn: str, *args) -> None:
        payload = ",".join(json.dumps(a) for a in args)
        self._eval(f"window.vz.{fn}({payload})")

    def _candidates(self):
        return list_service_bats("combined") + list_strategy_bats()

    def _active_candidates(self):
        """Кандидаты без «мёртвых» (0/5 на прошлом подборе). Никогда не пусто."""
        dead = set(self._config.dead_strategies or [])
        bats = self._candidates()
        active = [b for b in bats if b.name not in dead]
        return active if active else bats

    def _strat_name(self) -> str:
        name = Path(self._config.config_path).name if self._config.config_path else ""
        if not name:
            bats = self._candidates()
            name = bats[0].name if bats else "—"
        return name

    # ----------------------------------------------------------- pull (JS→PY)
    def get_meta(self) -> dict:
        return {
            "version": get_local_version(),
            "app": APP_VERSION,
            "strategy": self._strat_name(),
            "settings": {
                "autostart": autostart.is_enabled(),
                "autoconnect": self._config.autoconnect_zapret,
                "autoupdate": self._config.auto_update,
                "tray": self._config.minimize_to_tray,
            },
        }

    def warp_refresh(self) -> dict:
        connected, text = warp.status()
        return {"connected": connected, "text": text}

    def warp_toggle(self) -> None:
        connected, _ = warp.status()
        threading.Thread(
            target=warp.disconnect if connected else warp.connect, daemon=True).start()

    def warp_download(self) -> None:
        import webbrowser
        webbrowser.open("https://1.1.1.1/")

    # ----------------------------------------------------------- попап «Соединение»
    def connection_details(self) -> list:
        """Проверяет каждый сервис отдельно → [{name, ok, ms}] для попапа."""
        from concurrent.futures import ThreadPoolExecutor
        res = [None] * len(SERVICES)
        pool = ThreadPoolExecutor(max_workers=len(SERVICES))
        futs = {pool.submit(_probe_host, host, 3.0): i
                for i, (_name, host) in enumerate(SERVICES)}
        deadline = time.monotonic() + 5
        for fut, i in futs.items():
            try:
                ok, lat = fut.result(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:
                ok, lat = False, None
            res[i] = {"name": SERVICES[i][0], "ok": bool(ok),
                      "ms": round(lat) if lat else 0}
        pool.shutdown(wait=False)
        return [r for r in res if r]

    # ----------------------------------------------------------- живой пинг
    def ping_start(self) -> None:
        if self._ping_on:
            return
        self._ping_on = True
        threading.Thread(target=self._ping_loop, daemon=True).start()

    def ping_stop(self) -> None:
        self._ping_on = False

    def _ping_loop(self) -> None:
        while self._ping_on:
            ms = _tcp_ping()
            self._push("onPing", round(ms) if ms is not None else -1)
            time.sleep(1.0)

    # ----------------------------------------------------------- Дополнительно
    def advanced_status(self) -> dict:
        return {
            "game": zapret_flags.game_filter_mode(),   # off|all|tcp|udp
            "ipset": zapret_flags.ipset_loaded(),
            "quic": self._config.quic_disable,         # ручной выбор (движок форсит отдельно)
        }

    def _restart_if_running(self) -> None:
        """Перезапускает обход, чтобы winws перечитал флаги (game/ipset)."""
        if self._want:
            self._push("onState", "connecting", "Применяю настройки…")
            self._orch.start(self._active_candidates(), list(COMBINED_HOSTS),
                             preferred_name=self._strat_name())

    def set_game_filter(self, mode: str) -> dict:
        zapret_flags.set_game_filter(mode)
        self._restart_if_running()
        return self.advanced_status()

    def set_ipset(self, on: bool) -> dict:
        zapret_flags.set_ipset(bool(on))
        self._restart_if_running()
        return self.advanced_status()

    # ----------------------------------------------------------- апдейт zapret
    def check_zapret_update(self) -> dict:
        avail, cur, latest = updater.is_update_available()
        return {"available": avail, "current": cur, "latest": latest}

    # ----------------------------------------------------------- апдейт приложения
    def check_app_update(self) -> dict:
        avail, cur, latest = app_updater.is_update_available()
        return {"available": avail, "current": cur, "latest": latest}

    def app_update_now(self) -> None:
        threading.Thread(target=self._app_update_worker, daemon=True).start()

    def _app_update_worker(self) -> None:
        try:
            _tag, url = app_updater.get_latest()
            self._push("onAppUpdate", "Загрузка установщика…", -1.0)
            path = app_updater.download_installer(
                url, on_progress=lambda f: self._push("onAppUpdate", "", f))
            self._push("onAppUpdate", "Запуск установщика…", 1.0)
            app_updater.run_installer(path)
            self._push("onNotify", "Установщик запущен — приложение закроется для обновления")
            threading.Timer(2.0, self._quit).start()
        except app_updater.AppUpdateError as exc:
            self._push("onAppUpdate", f"Ошибка: {exc}", -2.0)

    def set_quic(self, on: bool) -> dict:
        on = bool(on)
        self._config.quic_disable = on
        self._config.save()
        self._engine.set_drop_quic(on)
        # Фаерволом больше НЕ пользуемся (QUIC дропает движок) — убираем любое
        # старое правило, чтобы оно не «залипло» после ребута.
        threading.Thread(target=lambda: quic.set_quic_disabled(False), daemon=True).start()
        # Если обход активен — перезапускаем, чтобы фильтр движка перечитал QUIC-режим.
        if self._want:
            self._start_epoch += 1
            self._engine.stop(); self._engine_on = False
            self._orch.stop()
            self._push("onState", "connecting", "Применяю QUIC-фикс…")
            threading.Thread(target=self._smart_start, args=(self._start_epoch,),
                             daemon=True).start()
        return {"game": zapret_flags.game_filter_mode(),
                "ipset": zapret_flags.ipset_loaded(), "quic": bool(on)}

    # ----------------------------------------------------------- DNS
    # force и provider НЕЗАВИСИМЫ: provider — выбор из списка (сохраняется),
    # force — отдельный шорткат «Google+OpenDNS». Выбор provider больше НЕ
    # сбрасывается на google и не слетает между запусками.
    def dns_status(self) -> dict:
        return {"force": self._config.dns_force, "provider": self._config.dns_provider}

    def dns_force(self, on: bool) -> None:
        self._config.dns_force = bool(on)
        self._config.save()
        threading.Thread(target=self._dns_apply, daemon=True).start()

    def dns_set(self, provider: str) -> None:
        if provider not in dns.PROVIDERS:
            return
        self._config.dns_provider = provider
        self._config.dns_force = False          # выбор провайдера снимает «принудительный»
        self._config.save()
        threading.Thread(target=self._dns_apply, daemon=True).start()

    def dns_reset(self) -> None:
        self._config.dns_provider = "dhcp"
        self._config.dns_force = False
        self._config.save()
        threading.Thread(target=self._dns_apply, daemon=True).start()

    def _dns_apply(self) -> None:
        """Применяет к системе текущее состояние из конфига (force | provider)."""
        if self._config.dns_force:
            dns.force_on()
        else:
            dns.set_provider(self._config.dns_provider)
        self._push("onDnsStatus", self.dns_status())

    def set_autostart(self, enabled: bool) -> bool:
        ok = autostart.set_enabled(enabled)
        self._config.autostart_windows = enabled
        self._config.save()
        return ok

    def set_autoconnect(self, enabled: bool) -> bool:
        self._config.autoconnect_zapret = enabled
        self._config.save()
        return True

    def set_autoupdate(self, enabled: bool) -> bool:
        self._config.auto_update = enabled
        self._config.save()
        return True

    def set_tray(self, enabled: bool) -> bool:
        self._config.minimize_to_tray = enabled
        self._config.save()
        return True

    # ----------------------------------------------------------- обход
    def toggle(self) -> bool:
        if self._tester.is_running():
            return False
        if self._want:
            self._want = False
            self._start_epoch += 1
            self._engine.stop(); self._engine_on = False
            self._orch.stop()
            self._push("onState", "off", "")
            return False
        if not self._active_candidates() and not VoidEngine.available():
            self._push("onState", "error", "Стратегии не найдены")
            return False
        self._want = True
        self._start_epoch += 1
        self._push("onState", "connecting", "Подключение…")
        threading.Thread(target=self._smart_start, args=(self._start_epoch,),
                         daemon=True).start()
        return True

    def _restore_quic(self) -> None:
        """Возвращает QUIC к ручному выбору пользователя (движок временно форсит)."""
        threading.Thread(
            target=lambda: quic.set_quic_disabled(self._config.quic_disable),
            daemon=True).start()

    def _smart_start(self, epoch: int) -> None:
        """Сначала пробуем НАШ движок (приоритет), иначе — combined/general (winws)."""
        # 1) Наш VoidEngine — калибровка по техникам.
        if VoidEngine.available():
            # Авто-сброс драйвера (чтобы не просить юзера перезапускать). QUIC движок
            # глушит сам, пока работает (UDP/443 дропается в WinDivert) — без правил
            # фаервола, поэтому после остановки/ребута ничего не остаётся заблокированным.
            reset_windivert()
            self._engine.set_drop_quic(self._config.quic_disable)  # форс-TCP по выбору
            best = self._engine_calibrate(epoch)
            if epoch != self._start_epoch or not self._want:
                return
            if best is not None:
                strat, ok, total, lat = best
                self._engine_on = True
                self._engine_strategy = strat
                self._engine_stats = (ok, total, lat)
                self._engine_start = time.time()
                ping = f"  ·  ~{lat:.0f} мс" if lat else ""
                self._push("onLog", f"✓ Стратегия {strat} работает ({ok}/{total})", "ok")
                self._push("onState", "running",
                           f"Стратегия {strat} · связь {ok}/{total}{ping}")
                threading.Thread(target=self._engine_watchdog, args=(epoch,),
                                 daemon=True).start()
                return
            self._engine.stop()
            self._push("onLog", "Не пробило напрямую — пробую запасную стратегию", "system")
        # 2) Фолбэк — оркестратор на .bat (combined + general).
        if epoch != self._start_epoch or not self._want:
            return
        bats = self._active_candidates()
        if not bats:
            self._push("onState", "error", "Ни свой метод, ни combined не сработали")
            self._want = False
            return
        self._orch.start(bats, list(COMBINED_HOSTS), preferred_name=self._strat_name())

    def _engine_calibrate(self, epoch: int):
        """Перебирает техники движка, возвращает первую рабочую (strat, ok, total, lat)."""
        hosts = list(COMBINED_HOSTS)
        for strat in engine_strategies.STRATEGIES:
            if epoch != self._start_epoch or not self._want:
                return None
            self._push("onState", "connecting", f"Подключение · {strat}…")
            self._push("onLog", f"[engine] пробую технику {strat}…", "system")
            self._engine.stop()
            kill_winws()
            time.sleep(0.5)
            self._engine.set_strategy(strat)
            if not self._engine.start():
                # авто-сброс драйвера и одна повторная попытка (конфликт версий WinDivert)
                self._push("onLog", "[engine] сбрасываю WinDivert и пробую снова…", "system")
                reset_windivert()
                time.sleep(1.0)
                if not self._engine.start():
                    self._push("onLog", "[engine] WinDivert недоступен — пропускаю движок", "error")
                    return None
            time.sleep(3.0)  # инициализация + прогрев
            ok, total, lat = probe_hosts(hosts, timeout=4.0)
            self._push("onLog", f"[engine] {strat}: связь {ok}/{total}"
                                + (f", ~{lat:.0f} мс" if ok else ""), "system")
            # Берём технику движка ТОЛЬКО если пробила ВСЁ — иначе уходим на combined
            # (надёжность важнее: для публичного релиза combined должен спасать).
            if total and ok == total:
                return (strat, ok, total, lat)
        return None

    def _engine_watchdog(self, epoch: int) -> None:
        while self._engine_on and epoch == self._start_epoch and self._want:
            time.sleep(25.0)
            if not (self._engine_on and epoch == self._start_epoch and self._want):
                return
            ok, total, lat = probe_hosts(list(COMBINED_HOSTS), timeout=4.0)
            self._engine_stats = (ok, total, lat) if ok else self._engine_stats
            if ok < max(1, (total + 1) // 2):
                self._push("onLog", "Связь просела — переключаю стратегию", "system")
                self._push("onState", "switching", "Переключение стратегии…")
                self._engine.stop(); self._engine_on = False
                if epoch == self._start_epoch and self._want:
                    self._orch.start(self._active_candidates(), list(COMBINED_HOSTS),
                                     preferred_name=self._strat_name())
                return

    def find_best(self) -> bool:
        if self._tester.is_running():
            self._tester.cancel()
            return False
        if self._want:
            self._want = False
            self._orch.stop()
        bats = self._candidates()
        if not bats:
            return False
        self._push("onTestStart")
        self._tester.start(
            on_log=lambda m: self._push("onLog", m, self._tag(m)),
            on_progress=lambda i, t, n: self._push("onTestProgress", i, t, n),
            on_config_done=lambda r: None,
            on_finished=self._on_test_finished,
            bats=bats, hosts=list(COMBINED_HOSTS),
        )
        return True

    # ----------------------------------------------------------- обновление
    def update_zapret(self) -> None:
        if self._updating:
            return
        self._updating = True
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self) -> None:
        try:
            version = updater.download_and_install(
                on_log=lambda m: self._push("onUpdate", m, -1),
                on_progress=lambda f: self._push("onUpdate", "", f),
            )
            self._config.last_installed_version = version
            self._config.save()
            self._push("onUpdateDone", True, version, get_local_version())
            self._push("onMeta", get_local_version(), self._strat_name())
        except updater.UpdateError as exc:
            self._push("onUpdateDone", False, str(exc), get_local_version())
        finally:
            self._updating = False

    # ----------------------------------------------------------- окно/трей
    def minimize(self) -> None:
        try:
            self._window.minimize()
        except Exception:
            pass

    def close_window(self) -> None:
        if self._config.minimize_to_tray and Tray.available():
            try:
                self._window.hide()
            except Exception:
                pass
            self._tray.start()
        else:
            self._quit()

    def _restore_from_tray(self) -> None:
        self._tray.stop()
        try:
            self._window.show()
        except Exception:
            pass

    def _quit(self) -> None:
        self._tray.stop()
        try:
            self._engine.stop()
            self._orch.stop()
        finally:
            try:
                self._window.destroy()
            except Exception:
                pass

    # ----------------------------------------------------------- старт UI
    def ready(self) -> None:
        self._push("onMeta", get_local_version(), self._strat_name())
        self._push("onState", "off", "")
        # Диагностика окружения в журнал.
        wp = get_winws_path()
        self._push("onLog", f"[i] Права администратора: {is_admin()}", "system")
        self._push("onLog", f"[i] winws.exe: {wp} (есть: {wp.is_file()})", "system")
        self._push("onLog", f"[i] Кандидатов стратегий: {len(self._candidates())}", "system")
        # ВАЖНО: на каждом старте сносим любое правило фаервола QUIC от старых версий —
        # иначе QUIC оставался заблокирован системно после ребута и YouTube/Discord
        # не грузились. Теперь QUIC дропает только сам движок, пока активен.
        threading.Thread(target=lambda: quic.set_quic_disabled(False), daemon=True).start()
        # Восстанавливаем выбранный DNS на систему (чтобы не «слетал» после перезапуска).
        if self._config.dns_force or self._config.dns_provider != "dhcp":
            threading.Thread(target=self._dns_apply, daemon=True).start()
        self.ping_start()   # живой пинг раз в секунду (для чипа и попапа)
        if self._config.autoconnect_zapret:
            threading.Timer(0.8, self.toggle).start()
        # Автообновление: тумблер «Автообновление» управляет И zapret, И самим
        # приложением VoidZapret.
        if self._config.auto_update:
            threading.Thread(target=self._auto_update, daemon=True).start()
            threading.Thread(target=self._auto_app_update, daemon=True).start()

    def _auto_app_update(self) -> None:
        """Если включено автообновление и вышла новая версия приложения — ставим."""
        avail, _cur, latest = app_updater.is_update_available()
        if avail:
            self._push("onNotify", f"Доступна VoidZapret {latest} — обновляю…")
            self._app_update_worker()   # скачает установщик, запустит, закроет приложение

    def _auto_update(self) -> None:
        try:
            avail, _local, latest = updater.is_update_available()
            if not avail:
                return
            updater.download_and_install(on_log=lambda _m: None)
            self._config.last_installed_version = latest
            self._config.save()
            self._push("onNotify", f"Установлена новая версия zapret {latest}")
            self._push("onMeta", get_local_version(), self._strat_name())
        except updater.UpdateError:
            return

    # ----------------------------------------------------------- колбэки
    @staticmethod
    def _tag(text: str) -> str:
        if text.startswith("[✓]"):
            return "ok"
        if text.startswith("[!]"):
            return "error"
        return "system"

    def _on_state(self, state: str, detail: str) -> None:
        if not self._want and state != ST_FAILED:
            return
        self._push("onState", state, detail)
        if state == ST_RUNNING:
            # Запоминаем стратегию, на которой реально подключились (автоподбор),
            # чтобы в следующий раз пробовать её первой → мгновенный старт.
            bat = self._orch.active_bat()
            if bat is not None:
                if bat.name in (self._config.dead_strategies or []):
                    self._config.dead_strategies = [
                        n for n in self._config.dead_strategies if n != bat.name]
                    self._config.save()
                if bat.name != Path(self._config.config_path).name:
                    self._config.config_path = bat.name
                    self._config.save()
                    self._push("onMeta", get_local_version(), bat.name)
        elif state == ST_FAILED:
            self._want = False

    def _on_log(self, text: str, tag: str) -> None:
        self._push("onLog", text, tag)

    def _on_test_finished(self, best, results) -> None:
        # Чистка: методы с 0/5 на полном подборе — в «мёртвые» (обычный запуск их
        # пропустит). Полный подбор всегда пересобирает этот список заново.
        try:
            self._config.dead_strategies = [
                r.name for r in (results or []) if r.total and r.ok == 0]
            self._config.save()
        except Exception:
            pass
        if best is None:
            self._push("onTestDone", False, "", 0, 0)
            return
        self._config.config_path = best.name
        self._config.save()
        self._push("onTestDone", True, best.name, best.ok, best.total)
        self._push("onMeta", get_local_version(), best.name)
        # Применяем найденный метод — сразу поднимаем обход с ним (а не только показываем).
        bats = self._candidates()
        if bats:
            self._want = True
            self._push("onState", "connecting", "Применяю лучший метод…")
            self._orch.start(bats, list(COMBINED_HOSTS), preferred_name=best.name)

    def _stats_loop(self) -> None:
        while True:
            time.sleep(1.0)
            if not self._want:
                continue
            if self._engine_on:
                ok, total, lat = self._engine_stats
                up = time.time() - self._engine_start if self._engine_start else 0
                self._push("onStats", ok, total, round(lat), round(up))
            elif self._orch.is_running():
                ok, total, lat = self._orch.get_stats()
                up = self._orch.get_uptime() or 0
                self._push("onStats", ok, total, round(lat), round(up))


def run() -> None:
    try:
        kill_winws()  # чистим зомби winws на старте
        reset_windivert()  # лечим битую службу WinDivert от прошлых/чужих обходов
    except Exception:
        pass
    api = Api()
    window = webview.create_window(
        "VoidZapret", url=_webui_path(), js_api=api,
        width=980, height=680, min_size=(860, 600),
        background_color="#08090E", frameless=True, easy_drag=False,
        resizable=True,
    )
    api.bind(window)
    webview.start(debug=False)


if __name__ == "__main__":
    run()
