# CLAUDE.md — контекст проекта для ИИ-ассистента

> Этот файл читается ИИ-ассистентами (Claude Code и др.) при старте, чтобы сразу
> понимать проект. Держи его в актуальном состоянии при крупных изменениях.

## Что это

**VoidZapret** — Windows-приложение для обхода DPI-блокировок (YouTube, Discord,
игры и т.д.). Тёмный интерфейс на **WebView2** (HTML/CSS/JS) поверх Python-ядра.
Основной метод обхода — **собственный движок VoidEngine** на драйвере WinDivert;
запасной — `combined`-стратегии через `winws.exe`.

- Репозиторий: `a3nuper/VoidZapret` (GitHub). Релизы: GitHub Releases (auto-update).
- Текущая версия: см. `config.APP_VERSION` (на момент написания — 3.2.9).
- Поддержка автора: https://boosty.to/a3nuper

## Архитектура

```
main.py            → повышает права (UAC) и запускает webapp.run()
webapp.py          → окно pywebview + класс Api (мост JS ↔ Python)
webui/             → фронт: index.html (рейл+вкладки), style.css (Midnight glass), app.js
core/
  engine/          → ★ НАШ движок VoidEngine (независим от winws)
    engine.py      → перехват TLS ClientHello (WinDivert/pydivert) + применение техники
    strategies.py  → техники десинка + генератор fake-ClientHello
    tls.py         → парсер ClientHello / SNI / точка сплита
    hosts.py       → встроенный список целевых доменов + матчинг SNI
  orchestrator.py  → запасной путь: перебор combined-.bat (winws) с самопроверкой+watchdog
  method_tester.py → probe_hosts/_detail (TLS-проверка связи), MethodTester (перебор .bat)
  process_manager.py → запуск/стоп winws, reset_windivert()
  updater.py       → обновление встроенного запасного набора стратегий
  app_updater.py   → авто-обновление приложения через GitHub Releases (GITHUB_REPO)
  dns.py, quic.py, warp.py, autostart.py → DNS-подмена, QUIC-фикс, WARP, автозапуск
  admin.py         → проверка/повышение прав
ui/tray.py         → иконка в системном трее (pystray)
zapret/            → winws.exe + WinDivert + combined-.bat + lists (для запасного пути)
installer/VoidZapret.iss → Inno Setup (+ тихий WebView2 bootstrapper)
VoidZapret.spec    → PyInstaller (onedir; collect_all webview/pythonnet/clr_loader/pydivert)
```

### Как работает обход (главное)

При нажатии «Запустить» → `Api._smart_start`:
1. **VoidEngine** (приоритет): калибровка — перебирает техники
   (`multifakedisorder/fakedisorder/multidisorder/disorder/fakesplit/fake/split/seqovl`),
   проверяет связь до `ENGINE_HOSTS` (реальные эндпоинты: youtube, googlevideo,
   youtubei, discord, gateway.discord.gg, cdn) через `probe_hosts_detail`, выбирает
   ЛУЧШУЮ технику. Принимает движок при `ok >= total//2`. Лучшая техника запоминается
   (`config.engine_strategy`): следующий старт берёт её СРАЗУ, без полного перебора;
   если просела ниже порога — стратегии пересобираются заново (и на старте, и в
   watchdog при просадке связи — он теперь пересобирает движок, а не сразу уходит в winws).
2. Если движок не открылся/не пробил → **combined** (winws) как фолбэк.

**Движок (engine.py) — критично:** WinDivert-фильтр ловит **ТОЛЬКО ClientHello**
(`tcp.Payload[0]==0x16 && tcp.Payload[5]==0x01`), НЕ весь трафик 443 — иначе весь
HTTPS идёт через Python-цикл, очередь переполняется, пакеты теряются и соединения
рвутся под нагрузкой. Десинк применяется только к ClientHello целевых SNI
(таргетинг по `hosts.py`), `ip.ident=0` (фикс Google/видео). QUIC (UDP/443) дропается
(форс-TCP), пока движок активен — иначе видео YouTube (googlevideo) уходит в HTTP/3
мимо TCP-десинка и режется DPI («страница есть, видео нет»). **ИСКЛЮЧЕНИЕ из QUIC-дропа:
диапазон Discord/Cloudflare `162.159.0.0/16`** (фильтр `ip.DstAddr < 162.159.0.0 or > ...`):
его QUIC НЕ глушим, иначе Discord-десктоп (Electron) залипает на «чёрной дыре» QUIC и не
подключается. ВАЖНО: TCP-ClientHello к Discord при этом ПО-ПРЕЖНЕМУ десинкается — Discord
заблокирован по SNI, без десинка рукопожатие режется (урок v3.2.8: отключил десинк Discord
по ошибке → связь упала до 2/6; в v3.2.9 десинк вернули, исключили ТОЛЬКО QUIC-дроп).
Дроп только внутри WinDivert-цикла, без правил фаервола (ничего не залипает после
ребута). Fake-техника шлёт поддельные ClientHello двух типов: badseq (per-packet DPI)
и correct-seq+снят ACK/datanoack (DPI со сборкой по seq), и каждый — на нескольких TTL
(`strategies.FAKE_TTLS`, «авто-TTL»: фейк безопасен при любом TTL, поэтому перебор
покрывает любую дистанцию до DPI). SNI фейка случайный (`_FAKE_SNIS`, анти-fingerprint).
Сплит — по midsld (середина домена 2-го уровня). Устойчивость к смене сети: `_net_monitor`
следит за сменой основного IP (Wi-Fi/VPN) и переподнимает обход. **Probe по скорости
(throughput) НЕ реализован**: требует экстрактор googlevideo (n-сигнатура YouTube), что
тяжело/хрупко — калибровка остаётся по handshake (на тротлящих сетях «слепа», но движок
бьёт тротлинг сильной техникой+QUIC-дроп+авто-TTL). **Голос Discord** (UDP к медиасерверам 66.22.192.0/18,
AS49544) — отдельный UDP-десинк: перед реальным пакетом шлём N фейковых STUN с низким
TTL (как winws `--dpi-desync-fake-stun`), реальный пакет ВСЕГДА форвардится без изменений
(нулевой риск регресса). Таргет по IP-диапазону, не по портам — чтобы не тащить через
Python-цикл игры/торренты. Игры/прочий UDP на других IP не трогаются.

## Сборка и запуск

- **Python только через `py`** (не `python` — это заглушка Microsoft Store).
- Запуск из исходников: `py main.py` (поднимет UAC).
- Сборка exe: `py -m PyInstaller --noconfirm VoidZapret.spec` → `dist/VoidZapret/`.
- Установщик: `& "C:\Users\a3nuper\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\VoidZapret.iss`.
- Все команды — из **корня репозитория**.

### Выпуск новой версии
Поднять версию в **4 местах**: `config.APP_VERSION`, `installer/VoidZapret.iss`
(`MyAppVersion`), `webui/app.js` (`appVer`), `webui/index.html` (`rail-ver`). Затем
собрать dist+installer, `git push`, и:
`gh release create vX.Y.Z dist\installer\VoidZapret-Setup-X.Y.Z.exe --latest`.

## Грабли (ОБЯЗАТЕЛЬНО учитывать)

- **Запущенный VoidZapret.exe держит `dist\`** (elevated) — пересборка падает с
  WinError 5. Перед сборкой закрыть приложение полностью (трей → Выход). Из
  не-elevated шелла убить нельзя; просить пользователя или elevated `taskkill`.
- **WinDivert64.sys / ClrLoader.dll залочены** если драйвер/exe загружены: перед
  сборкой `sc stop windivert/WinDivert` + закрыть exe.
- **`.bat` строго CRLF + ASCII-комментарии** (см. `.gitattributes`) — иначе cmd
  ломает разбор после `chcp 65001`. combined-.bat генерируются с `\r\n`.
- **Битая/чужая служба WinDivert** (от другого DPI-обхода) → `reset_windivert()`
  (`sc delete windivert/windivert14`); вызывается на старте и в калибровке.
- **Мост WebView**: пуши в JS только через `Api._eval` (сериализован локом —
  параллельные `evaluate_js` дедлочат WebView2). JS зовёт публичные методы `Api`.
- **Смоук UI без прав**: создать окно в потоке и `win.destroy()` через пару секунд;
  проверять РЕАЛЬНЫЙ путь (запуск движка/оркестратора), а не только `window.vz.*`.
- **pydivert/WinDivert** ставится через `pip install pydivert`; в сборку тянется
  через `collect_all('pydivert')` (его `windivert_dll/WinDivert64.dll/.sys`).

## Стиль/язык
UI и комментарии — на русском. Код — Python 3.10+ / ванильный JS. Без тяжёлых
фронт-фреймворков. Тема только тёмная (фишка проекта).
