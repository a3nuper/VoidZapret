"""Техники десинхронизации VoidEngine и генератор поддельного ClientHello.

Чистые байты/параметры, без pydivert. Сборку реальных пакетов делает engine.py.
"""

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
