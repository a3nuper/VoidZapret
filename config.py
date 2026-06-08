"""Загрузка/сохранение конфигурации и детект путей встроенной папки zapret."""

import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

CONFIG_FILENAME = "zapret_gui_config.json"

# Версия самого приложения VoidZapret (для авто-обновления через GitHub Releases).
APP_VERSION = "3.2.5"

# Имена .bat в порядке приоритета для автодетекта (оставлен только курируемый набор).
BAT_PRIORITY = [
    "general (ALT10).bat",
    "general (ALT11).bat",
    "general (ALT12).bat",
]

# general-стратегии больше не используем (наш движок + combined-фолбэк) — после
# обновления zapret все general удаляются.
KEEP_GENERAL_BATS: set = set()

# Файлы, которые не являются стратегиями обхода.
NON_STRATEGY_BATS = {"service.bat"}

# Префиксы наших собственных .bat (combined*), которых нет в релизе Flowseal —
# их сохраняем/восстанавливаем при обновлении.
CUSTOM_BAT_PREFIXES = ("combined",)


def get_base_dir() -> Path:
    """Каталог рядом с exe (frozen) либо корень проекта zapret-gui (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def get_icon_path() -> Path | None:
    """Путь к icon.ico (иконка приложения): из _MEIPASS (frozen) либо из исходников."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "icon.ico").exists():
        return Path(meipass) / "icon.ico"
    candidate = get_base_dir() / "icon.ico"
    return candidate if candidate.exists() else None


def _candidate_zapret_dirs() -> list[Path]:
    """Все места, где может лежать встроенная папка zapret, по приоритету."""
    candidates: list[Path] = []

    # 1. Распакованная PyInstaller --onefile (встроенная копия).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "zapret")

    base = get_base_dir()
    # 2. Рядом с exe / в корне проекта.
    candidates.append(base / "zapret")
    # 3. Внутри dist (старое расположение) и на уровень выше.
    candidates.append(base / "dist" / "zapret")
    candidates.append(base.parent / "zapret")

    # Убираем дубликаты, сохраняя порядок.
    seen: set[str] = set()
    unique: list[Path] = []
    for c in candidates:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _is_valid_zapret(path: Path) -> bool:
    """Папка считается рабочей копией zapret, если в ней есть winws.exe."""
    return (path / "bin" / "winws.exe").is_file()


def get_writable_zapret_dir() -> Path:
    """Постоянная (записываемая) папка zapret рядом с exe — туда ставятся обновления.

    В onefile-сборке встроенная копия лежит в _MEIPASS (временная, пропадает при
    выходе), поэтому обновления нужно класть сюда — рядом с исполняемым файлом.
    """
    return get_base_dir() / "zapret"


def get_zapret_dir() -> Path:
    """Активная папка zapret.

    Приоритет у постоянной внешней копии (если в ней есть winws.exe) — это
    позволяет применённым обновлениям вступать в силу. Иначе — встроенная копия.
    """
    external = get_writable_zapret_dir()
    if _is_valid_zapret(external):
        return external
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and _is_valid_zapret(Path(meipass) / "zapret"):
        return Path(meipass) / "zapret"
    for candidate in _candidate_zapret_dirs():
        if candidate.is_dir():
            return candidate
    return external


def get_bundled_zapret_dir() -> Path | None:
    """Встроенная копия (в _MEIPASS или в исходниках) — источник наших custom .bat."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "zapret").is_dir():
        return Path(meipass) / "zapret"
    for candidate in _candidate_zapret_dirs():
        if candidate.is_dir():
            return candidate
    return None


def get_local_version() -> str:
    """Текущая версия zapret из service.bat (LOCAL_VERSION) или 'неизвестно'."""
    service = get_zapret_dir() / "service.bat"
    if service.is_file():
        try:
            text = service.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'set\s+"LOCAL_VERSION=([^"]+)"', text)
            if m:
                return m.group(1).strip()
        except OSError:
            pass
    return "неизвестно"


def get_winws_path() -> Path:
    """Путь к winws.exe внутри папки zapret."""
    return get_zapret_dir() / "bin" / "winws.exe"


def list_strategy_bats() -> list[Path]:
    """Стратегии general*.bat для подбора лучшего метода (без служебных).

    App-специфичные стратегии (discord.bat, dbd.bat) намеренно исключены —
    их не нужно сравнивать в общем переборе.
    """
    zdir = get_zapret_dir()
    if not zdir.is_dir():
        return []
    bats = [p for p in zdir.glob("general*.bat")]
    return sorted(bats, key=lambda p: p.name.lower())


def list_service_bats(prefix: str) -> list[Path]:
    """Все .bat указанного сервиса (например 'dbd' или 'discord'), по имени.

    База (dbd.bat / discord.bat) идёт первой, остальные — по алфавиту.
    """
    zdir = get_zapret_dir()
    if not zdir.is_dir():
        return []
    bats = list(zdir.glob(f"{prefix}*.bat"))

    def sort_key(p: Path) -> tuple[int, str]:
        is_base = p.stem.lower() == prefix.lower()
        return (0 if is_base else 1, p.name.lower())

    return sorted(bats, key=sort_key)


def auto_detect_best_bat() -> Path | None:
    """Выбирает .bat по приоритету из папки zapret."""
    zdir = get_zapret_dir()
    if not zdir.is_dir():
        return None
    for name in BAT_PRIORITY:
        candidate = zdir / name
        if candidate.is_file():
            return candidate
    bats = list_strategy_bats()
    return bats[0] if bats else None


@dataclass
class Config:
    """Все настройки приложения."""

    config_path: str = ""
    window_geometry: str = "1080x720"
    autostart_windows: bool = False
    autoconnect_zapret: bool = False
    auto_update: bool = False
    minimize_to_tray: bool = True
    dns_provider: str = "dhcp"
    dns_force: bool = False
    quic_disable: bool = False        # ручной выбор «QUIC-фикс» (движок временно форсит)
    # Стратегии, давшие 0/5 при последнем полном подборе — пропускаем их в
    # обычном «Запустить» (чтобы не тратить время). Сбрасывается при «Подобрать».
    dead_strategies: list = field(default_factory=list)
    last_installed_version: str = ""
    # Выбранный профиль обхода оркестратора (combined / web / discord / dbd).
    profile: str = "combined"
    # Предпочтительная стратегия (.bat) для каждого профиля — её оркестратор
    # пробует первой. config_path хранит стратегию веб-профиля (legacy-имя).
    discord_strategy: str = ""
    dbd_strategy: str = ""
    combined_strategy: str = ""

    @classmethod
    def load(cls) -> "Config":
        """Читает конфиг из JSON, возвращает дефолт если файла нет."""
        path = get_base_dir() / CONFIG_FILENAME
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in cls.__dataclass_fields__.values()}
            return cls(**{k: v for k, v in data.items() if k in known})
        except (json.JSONDecodeError, TypeError, OSError):
            return cls()

    def save(self) -> None:
        """Сохраняет текущие настройки в JSON."""
        path = get_base_dir() / CONFIG_FILENAME
        try:
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
