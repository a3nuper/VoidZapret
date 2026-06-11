"""Техники десинхронизации VoidEngine и генератор поддельного ClientHello.

Чистые байты/параметры, без pydivert. Сборку реальных пакетов делает engine.py.
"""

import ipaddress
import os
import random

from config import get_zapret_dir

# Порядок калибровки: сильные/проверенные техники первыми. Первая прошедшая —
# рабочий метод для данного провайдера.
STRATEGIES = ["multifakedisorder", "fakedisorder", "multidisorder", "disorder",
              "fakesplit", "fake", "split", "seqovl"]

# Низкий TTL для fake-пакета (умирает в пути после DPI, не доходит до сервера).
FAKE_TTL = 8
# «Авто-TTL»: перебор TTL для фейков. Шлём фейки с разным TTL, чтобы хотя бы один
# гарантированно дошёл до DPI независимо от его дистанции (близко/далеко). Фейки
# безопасны при ЛЮБОМ TTL (badseq/datanoack/низкий-TTL не дают серверу их принять),
# поэтому перебор ничем не грозит реальному соединению — только покрывает больше сетей.
FAKE_TTLS = (4, 7, 10)
# Сколько байт «мусора» накладывать в seqovl.
SEQOVL = 8

# Невинные SNI для фейков — выбираем СЛУЧАЙНО (анти-fingerprint): чтобы DPI не мог
# зафиксировать сам обход по постоянному «www.google.com».
_FAKE_SNIS = (
    "www.google.com", "www.microsoft.com", "www.apple.com", "www.cloudflare.com",
    "www.bing.com", "www.amazon.com", "www.office.com", "www.windowsupdate.com",
    "www.wikipedia.org", "www.icloud.com",
)


def build_fake_clienthello(sni: str | None = None) -> bytes:
    """Минимальный правдоподобный TLS ClientHello с «невинным» SNI.

    DPI, увидев его первым (на том же seq), считает соединение разрешённым. SNI по
    умолчанию случайный из списка, а длина session_id рандомная — это меняет
    фингерпринт фейка от пакета к пакету (анти-fingerprint самого обхода).
    """
    if sni is None:
        sni = random.choice(_FAKE_SNIS)
    name = sni.encode("ascii", "ignore")
    entry = b"\x00" + len(name).to_bytes(2, "big") + name          # type host_name
    slist = len(entry).to_bytes(2, "big") + entry
    sni_ext = b"\x00\x00" + len(slist).to_bytes(2, "big") + slist  # ext server_name
    exts = len(sni_ext).to_bytes(2, "big") + sni_ext
    sid = os.urandom(random.choice((0, 16, 32)))                   # случайный session_id
    body = (
        b"\x03\x03"                       # client_version TLS 1.2
        + os.urandom(32)                  # random
        + bytes([len(sid)]) + sid         # session_id (случайной длины)
        + b"\x00\x02\x13\x01"             # cipher_suites: TLS_AES_128_GCM_SHA256
        + b"\x01\x00"                     # compression: null
        + exts
    )
    hs = b"\x01" + len(body).to_bytes(3, "big") + body             # handshake ClientHello
    rec = b"\x16\x03\x01" + len(hs).to_bytes(2, "big") + hs        # TLS record
    return rec


# ------------------------------------------------------------- Discord-голос (UDP)
# Голосовые/медиа-серверы Discord (AS49544) — диапазон 66.22.192.0/18. UDP-десинк
# голоса применяем ТОЛЬКО к этому диапазону: объём низкий (один звонок), Python-цикл
# не захлёбывается, а игры/торренты на других IP вообще не трогаются.
DISCORD_VOICE_CIDR = "66.22.192.0/18"
_VOICE_NET = ipaddress.ip_network(DISCORD_VOICE_CIDR)
VOICE_LO_IP = str(_VOICE_NET.network_address)       # "66.22.192.0"  (для WinDivert-фильтра)
VOICE_HI_IP = str(_VOICE_NET.broadcast_address)     # "66.22.255.255"
VOICE_LO_INT = int(_VOICE_NET.network_address)      # для быстрой проверки в _handle
VOICE_HI_INT = int(_VOICE_NET.broadcast_address)

# На скольких первых пакетах каждого голосового потока (по dst IP) делать десинк
# (как winws --dpi-desync-cutoff). На каждом из них шлём фейк-STUN по всем FAKE_TTLS.
VOICE_CUTOFF = 8

# Фронтенд Discord: домены discord.com / gateway.discord.gg / cdn.discordapp.com /
# discord.media — все на Cloudflare 162.159.0.0/16. Из этого диапазона движок исключает
# ТОЛЬКО QUIC-дроп (его QUIC не глушим — иначе Discord-десктоп/Electron залипает на
# «чёрной дыре» QUIC). TCP-ClientHello к Discord ПО-ПРЕЖНЕМУ десинкается — Discord
# заблокирован по SNI, десинк обязателен. YouTube не затрагивается (googlevideo — другой
# ASN, не Cloudflare).
DISCORD_FRONT_CIDR = "162.159.0.0/16"
_FRONT_NET = ipaddress.ip_network(DISCORD_FRONT_CIDR)
FRONT_LO_IP = str(_FRONT_NET.network_address)       # "162.159.0.0"  (для QUIC-фильтра)
FRONT_HI_IP = str(_FRONT_NET.broadcast_address)     # "162.159.255.255"


def build_fake_stun() -> bytes:
    """Фейковый STUN Binding Request (RFC 5389), 20 байт — декой для UDP-десинка
    голоса Discord. DPI, увидев в начале потока «обычный STUN», не классифицирует
    его как Discord-voice и не душит. Низкий TTL (ставит engine) убивает пакет до
    сервера — реальный голос не затрагивается."""
    return (
        b"\x00\x01"            # message type: Binding Request
        + b"\x00\x00"          # message length: 0 атрибутов
        + b"\x21\x12\xa4\x42"   # magic cookie
        + os.urandom(12)       # transaction id
    )


# ------------------------------------------------------------- реальные winws-фейки
# Готовые крафченые fake-payload'ы из zapret/bin (как winws --dpi-desync-fake-*):
# РЕАЛЬНЫЙ ClientHello к разрешённому RU-сайту (4pda.to / max.ru) РФ-DPI охотнее
# принимает за «легальное» соединение, чем минимальный синтетический фейк — это и
# нужно для пробития Discord-gateway и др. Fallback — синтетика (движок остаётся
# работоспособным и без этих файлов). Загружается один раз и кэшируется.
_BIN_CACHE: dict[str, bytes] = {}
_REAL_TLS_FILES = ("tls_clienthello_4pda_to.bin", "tls_clienthello_max_ru.bin",
                   "tls_clienthello_www_google_com.bin")


def _load_bin(name: str) -> bytes:
    if name not in _BIN_CACHE:
        data = b""
        try:
            p = get_zapret_dir() / "bin" / name
            if p.is_file():
                data = p.read_bytes()
        except Exception:
            data = b""
        _BIN_CACHE[name] = data
    return _BIN_CACHE[name]


def fake_clienthello_payload() -> bytes:
    """Реалистичный fake-ClientHello (крафч из zapret/bin, случайный), иначе синтетика."""
    reals = [d for f in _REAL_TLS_FILES if (d := _load_bin(f))[:1] == b"\x16"]
    return random.choice(reals) if reals else build_fake_clienthello()


def fake_stun_payload() -> bytes:
    """Крафченый STUN (zapret/bin/stun.bin) или синтетический."""
    d = _load_bin("stun.bin")
    return d if d[:2] == b"\x00\x01" else build_fake_stun()
