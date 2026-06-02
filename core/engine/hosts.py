"""Таргетинг по хостам: десинк применяем только к заблокированным SNI.

Список целей берём из zapret/lists (general + google) + домены Discord/EOS.
Так не трогаем «чистый» трафик → меньше накладных и ниже пинг.
"""

from config import get_zapret_dir

_EXTRA = (
    "discord.com,discord.gg,discord.media,discordapp.com,discordapp.net,discord.app,"
    "discordcdn.com,dis.gd,epicgames.com,epicgames.dev,unrealengine.com,fortnite.com"
).split(",")


def load_targets() -> set[str]:
    doms: set[str] = set(d.strip().lower() for d in _EXTRA if d.strip())
    lists_dir = get_zapret_dir() / "lists"
    for fname in ("list-general.txt", "list-general-user.txt", "list-google.txt"):
        p = lists_dir / fname
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    doms.add(line)
        except OSError:
            pass
    return doms


def matches(sni: str, targets: set[str]) -> bool:
    """SNI попадает под обход, если совпадает с доменом или его поддоменом."""
    if not targets:
        return True
    sni = sni.lower().rstrip(".")
    if sni in targets:
        return True
    return any(sni.endswith("." + d) for d in targets)
