"""Техники десинхронизации VoidEngine и генератор поддельного ClientHello.

Чистые байты/параметры, без pydivert. Сборку реальных пакетов делает engine.py.
"""

import ipaddress
import os

# Порядок калибровки: сильные/проверенные техники первыми. Первая прошедшая —
# рабочий метод для данного провайдера.
STRATEGIES = ["multifakedisorder", "fakedisorder", "multidisorder", "disorder",
              "fakesplit", "fake", "split", "seqovl"]

# Низкий TTL для fake-пакета (умирает в пути после DPI, не доходит до сервера).
FAKE_TTL = 8
# Сколько байт «мусора» накладывать в seqovl.
SEQOVL = 8

_FAKE_SNI = "www.google.com"


def build_fake_clienthello(sni: str = _FAKE_SNI) -> bytes:
    """Минимальный правдоподобный TLS ClientHello с «невинным» SNI.

    DPI, увидев его первым (на том же seq), считает соединение разрешённым.
    """
    name = sni.encode("ascii", "ignore")
    entry = b"\x00" + len(name).to_bytes(2, "big") + name          # type host_name
    slist = len(entry).to_bytes(2, "big") + entry
    sni_ext = b"\x00\x00" + len(slist).to_bytes(2, "big") + slist  # ext server_name
    exts = len(sni_ext).to_bytes(2, "big") + sni_ext
    body = (
        b"\x03\x03"                       # client_version TLS 1.2
        + os.urandom(32)                  # random
        + b"\x00"                         # session_id len 0
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

# Сколько фейковых STUN слать перед реальным голосовым пакетом и на скольких первых
# пакетах каждого потока (как winws: --dpi-desync-repeats / --dpi-desync-cutoff).
VOICE_REPEATS = 6
VOICE_CUTOFF = 8


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
