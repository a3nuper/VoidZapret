# CLAUDE.md — контекст проекта для ИИ-ассистента

> Этот файл читается ИИ-ассистентами (Claude Code и др.) при старте, чтобы сразу
> понимать проект. Держи его в актуальном состоянии при крупных изменениях.

## Что это

**VoidZapret** — Windows-приложение для обхода DPI-блокировок (YouTube, Discord,
игры и т.д.). Тёмный интерфейс на **WebView2** (HTML/CSS/JS) поверх Python-ядра.
Основной метод обхода — **собственный движок VoidEngine** на драйвере WinDivert;
запасной — `combined`-стратегии через `winws.exe`.

- Репозиторий: `a3nuper/VoidZapret` (GitHub). Релизы: GitHub Releases (auto-update).
- Текущая версия: см. `config.APP_VERSION` (на момент написания — 3.2.3).
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
   ЛУЧШУЮ технику. Принимает движок при `ok >= total//2`.
2. Если движок не открылся/не пробил → **combined** (winws) как фолбэк.

**Движок (engine.py) — критично:** WinDivert-фильтр ловит **ТОЛЬКО ClientHello**
(`tcp.Payload[0]==0x16 && tcp.Payload[5]==0x01`), НЕ весь трафик 443 — иначе весь
HTTPS идёт через Python-цикл, очередь переполняется, пакеты теряются и соединения
рвутся под нагрузкой. Десинк применяется только к ClientHello целевых SNI
(таргетинг по `hosts.py`), `ip.ident=0` (фикс Google/видео). QUIC (UDP/443) дропается
ВСЕГДА, пока движок активен (форс-TCP) — иначе видео YouTube (googlevideo) и часть
Discord уходят в HTTP/3 мимо TCP-десинка и режутся DPI («страница есть, видео нет»).
Дроп только внутри WinDivert-цикла, без правил фаервола (ничего не залипает после
ребута). Fake-техника шлёт ДВА поддельных ClientHello: badseq (per-packet DPI) и
correct-seq+низкий TTL+снят ACK/datanoack (DPI со сборкой по seq). Сплит — по midsld
(середина домена 2-го уровня).

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
