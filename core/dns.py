"""Управление системным DNS — подмена на анти-блокировочные серверы.

Метод обхода «как у конкурентов»: прописывает на активные сетевые адаптеры
публичные DNS (Google + OpenDNS либо выбранного провайдера), что обходит
блокировки уровня DNS у провайдера. Для Win11 — best-effort включение DoH.

Использует PowerShell-командлеты Set/Get-DnsClientServerAddress (не зависят от
языка системы). Требует прав администратора (они у приложения есть).
"""

import subprocess

_NO_WINDOW = subprocess.CREATE_NO_WINDOW

# Провайдеры для списка «DNS-серверы».
PROVIDERS = {
    "dhcp":       {"name": "Автоматически (DHCP)", "servers": [], "doh": ""},
    "cloudflare": {"name": "Cloudflare", "servers": ["1.1.1.1", "1.0.0.1"],
                   "doh": "https://cloudflare-dns.com/dns-query"},
    "google":     {"name": "Google DNS", "servers": ["8.8.8.8", "8.8.4.4"],
                   "doh": "https://dns.google/dns-query"},
    "dnssb":      {"name": "Dns.SB", "servers": ["185.222.222.222", "45.11.45.11"],
                   "doh": "https://doh.dns.sb/dns-query"},
    # Безопасные
    "quad9":      {"name": "Quad9", "servers": ["9.9.9.9", "149.112.112.112"],
                   "doh": "https://dns.quad9.net/dns-query"},
    "adguard":    {"name": "AdGuard", "servers": ["94.140.14.14", "94.140.15.15"],
                   "doh": "https://dns.adguard-dns.com/dns-query"},
    "opendns":    {"name": "OpenDNS", "servers": ["208.67.222.222", "208.67.220.220"],
                   "doh": "https://doh.opendns.com/dns-query"},
    "dnsdoh":     {"name": "dnsdoh.art", "servers": ["194.180.189.33"],
                   "doh": "https://dnsdoh.art/dns-query"},
    # Для ИИ (разблокировка ChatGPT и т.п.)
    "xbox":       {"name": "Xbox DNS", "servers": ["176.99.11.77"], "doh": ""},
    "comss":      {"name": "Comss DNS", "servers": ["83.220.169.155"],
                   "doh": "https://dns.comss.one/dns-query"},
    "malw":       {"name": "dns.malw.link", "servers": ["84.21.189.133"],
                   "doh": "https://dns.malw.link/dns-query"},
}

# «Принудительный DNS» — связка Google + OpenDNS.
FORCE_SERVERS = ["8.8.8.8", "208.67.222.222", "8.8.4.4", "208.67.220.220"]


def _ps(script: str) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ps_out(script: str) -> str:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def active_adapters() -> list[str]:
    """Имена активных (Up) сетевых адаптеров."""
    out = _ps_out(
        "Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} "
        "| Select-Object -ExpandProperty Name")
    names = [l.strip() for l in out.splitlines() if l.strip()]
    if names:
        return names
    # запасной путь — все Up-адаптеры (включая виртуальные)
    out = _ps_out(
        "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} "
        "| Select-Object -ExpandProperty Name")
    return [l.strip() for l in out.splitlines() if l.strip()]


def _alias(a: str) -> str:
    return a.replace("'", "''")


def set_servers(servers: list[str]) -> bool:
    """Прописывает servers на все активные адаптеры. [] = сброс на DHCP."""
    aliases = active_adapters()
    if not aliases:
        return False
    if not servers:
        return reset_dhcp()
    addrs = ",".join(f"'{s}'" for s in servers)
    parts = [
        f"Set-DnsClientServerAddress -InterfaceAlias '{_alias(a)}' "
        f"-ServerAddresses @({addrs})"
        for a in aliases
    ]
    parts.append("Clear-DnsClientCache")
    return _ps("; ".join(parts))


def reset_dhcp() -> bool:
    """Сбрасывает DNS на автоматический (DHCP) на всех активных адаптерах."""
    aliases = active_adapters()
    if not aliases:
        return False
    parts = [
        f"Set-DnsClientServerAddress -InterfaceAlias '{_alias(a)}' -ResetServerAddresses"
        for a in aliases
    ]
    parts.append("Clear-DnsClientCache")
    return _ps("; ".join(parts))


def apply_doh(server: str, template: str) -> None:
    """Best-effort регистрация DoH-шаблона (Windows 11). Ошибки игнорируются."""
    if not template:
        return
    _ps(f"netsh dns add encryption server={server} dohtemplate={template} "
        f"autoupgrade=yes udpfallback=no")


def set_provider(provider: str) -> bool:
    """Применяет провайдера из PROVIDERS (+ DoH best-effort)."""
    info = PROVIDERS.get(provider)
    if info is None:
        return False
    if not info["servers"]:
        return reset_dhcp()
    ok = set_servers(info["servers"])
    if ok and info.get("doh"):
        for s in info["servers"]:
            apply_doh(s, info["doh"])
    return ok


def force_on() -> bool:
    """Принудительный DNS: Google + OpenDNS."""
    return set_servers(FORCE_SERVERS)
