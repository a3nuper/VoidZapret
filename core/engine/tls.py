"""Разбор TLS ClientHello: детект + поиск SNI (для точки сплита).

Всё в чистом Python, без зависимостей — парсим байты TCP-пейлоада.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClientHelloInfo:
    sni: str                 # имя хоста из SNI ('' если нет)
    sni_offset: int          # абсолютный сдвиг начала имени в пейлоаде
    sni_len: int             # длина имени в байтах


def is_client_hello(payload: bytes) -> bool:
    """TLS handshake-запись с ClientHello: 16 03 0X .. .. 01 ..."""
    return (
        len(payload) >= 6
        and payload[0] == 0x16          # content_type = handshake
        and payload[1] == 0x03          # major version 3
        and payload[5] == 0x01          # handshake type = ClientHello
    )


def parse_client_hello(payload: bytes) -> Optional[ClientHelloInfo]:
    """Возвращает инфо о SNI или None. Безопасен к битым данным."""
    if not is_client_hello(payload):
        return None
    try:
        p = payload
        n = len(p)
        # TLS record(5) + handshake header(4) + client_version(2) + random(32)
        i = 5 + 4 + 2 + 32
        if i + 1 > n:
            return None
        # session_id
        sid_len = p[i]; i += 1 + sid_len
        # cipher_suites
        if i + 2 > n:
            return None
        cs_len = (p[i] << 8) | p[i + 1]; i += 2 + cs_len
        # compression_methods
        if i + 1 > n:
            return None
        cm_len = p[i]; i += 1 + cm_len
        # extensions
        if i + 2 > n:
            return None
        ext_total = (p[i] << 8) | p[i + 1]; i += 2
        end = min(n, i + ext_total)
        while i + 4 <= end:
            etype = (p[i] << 8) | p[i + 1]
            elen = (p[i + 2] << 8) | p[i + 3]
            i += 4
            if etype == 0x0000:  # server_name
                # server_name_list: list_len(2), entry: type(1)+name_len(2)+name
                j = i
                if j + 2 > n:
                    return None
                j += 2  # list length
                if j + 3 > n:
                    return None
                name_type = p[j]; j += 1
                name_len = (p[j] << 8) | p[j + 1]; j += 2
                if name_type != 0x00 or j + name_len > n:
                    return None
                sni = p[j:j + name_len].decode("utf-8", "ignore") if name_len else ""
                return ClientHelloInfo(sni=sni, sni_offset=j, sni_len=name_len)
            i += elen
    except Exception:
        return None
    return None


def _sld_offset(host: str) -> int:
    """Сдвиг начала домена 2-го уровня (SLD) внутри host.

    Для «redirector.googlevideo.com» SLD = «googlevideo»; для «gateway.discord.gg»
    SLD = «discord». Разрыв внутри SLD надёжнее ломает SNI-сигнатуру DPI, чем
    середина всего имени (которая часто попадает в поддомен). 0, если меток < 2.
    """
    labels = host.split(".")
    if len(labels) < 2:
        return 0
    tld, sld = labels[-1], labels[-2]
    # host оканчивается на «…sld.tld» → начало SLD = (длина host) − len(tld) − 1(точка) − len(sld).
    return max(0, len(host) - len(tld) - 1 - len(sld))


def split_position(payload: bytes) -> int:
    """Где резать ClientHello: середина домена 2-го уровня (midsld), иначе фикс-сдвиг.

    Разрыв внутри SLD не даёт DPI собрать SNI-сигнатуру. Для одноимённых
    публичных суффиксов (bbc.co.uk) midsld может попасть в «co» — разрыв всё равно
    ломает сигнатуру, точность тут не критична.
    """
    info = parse_client_hello(payload)
    if info and info.sni and info.sni_len >= 2:
        sld_off = _sld_offset(info.sni)
        sld_len = len(info.sni.split(".")[-2]) if info.sni.count(".") >= 1 else info.sni_len
        if sld_len >= 2:
            pos = info.sni_offset + sld_off + sld_len // 2
            if 0 < pos < len(payload):
                return pos
        # запасной вариант внутри SNI — середина всего имени
        return info.sni_offset + info.sni_len // 2
    # запасной вариант — режем почти в начале записи (после заголовка)
    return min(len(payload) - 1, 6) if len(payload) > 6 else max(1, len(payload) // 2)
