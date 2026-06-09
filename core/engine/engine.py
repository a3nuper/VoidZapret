"""VoidEngine — независимый движок обхода DPI на WinDivert (pydivert).

Перехватывает исходящий TLS ClientHello (TCP/443) и применяет выбранную технику
десинхронизации. Техники (см. strategies.py):
  • split      — разрыв ClientHello внутри SNI на два сегмента;
  • disorder   — те же сегменты, но в обратном порядке;
  • fake       — поддельный ClientHello (невинный SNI, битый checksum + низкий TTL)
                 перед реальным — отравляет состояние DPI;
  • fakesplit  — fake + split (самая сильная);
  • seqovl     — наложение seq (мусор «перекрывает» начало) перед реальными данными.
Плюс таргетинг по хостам: десинк только для заблокированных SNI (ниже пинг).

Требует прав администратора. Не запускать одновременно с winws.
"""

import socket
import struct
import threading
from typing import Callable, Optional

from core.engine.tls import is_client_hello, parse_client_hello, split_position
from core.engine import strategies, hosts

try:
    import pydivert
    _AVAILABLE = True
except Exception:
    pydivert = None
    _AVAILABLE = False

_MASK = 0xFFFFFFFF


class VoidEngine:
    # КЛЮЧЕВОЕ: ловим ТОЛЬКО TLS ClientHello (record=0x16, handshake=0x01 на offset 5),
    # а не весь трафик 443. Иначе через Python-цикл шёл бы ВЕСЬ HTTPS → захлёбывание и
    # потеря пакетов под нагрузкой (Discord/видео рвались). Теперь 99.9% пакетов идут
    # мимо WinDivert напрямую — обход лёгкий и не ломает соединения.
    _CH = "tcp.DstPort == 443 and tcp.Payload[0] == 0x16 and tcp.Payload[5] == 0x01"
    # Голосовые/медиа-серверы Discord (AS49544) — узкий IP-диапазон, чтобы UDP-десинк
    # голоса не тащил через Python-цикл игры/торренты (низкий объём = без перегруза).
    _VOICE_RANGE = (f"ip.DstAddr >= {strategies.VOICE_LO_IP} and "
                    f"ip.DstAddr <= {strategies.VOICE_HI_IP}")
    _VOICE = f"(udp and ({_VOICE_RANGE}))"
    # QUIC-дроп (форс-TCP) для ВСЕХ, КРОМЕ диапазона Discord/Cloudflare (162.159.x):
    # его QUIC НЕ дропаем (Discord-десктоп/Electron не залипает на чёрной дыре QUIC),
    # а вот TCP-ClientHello к Discord по-прежнему ДЕСИНКается (Discord заблокирован по
    # SNI, десинк обязателен — без него рукопожатие режется). Discord-голос (66.22.x)
    # ловится _VOICE и уходит в voice-десинк. `not` фильтр не поддерживает — исключаем
    # диапазон через `< lo or > hi`.
    _QUIC = (f"(udp.DstPort == 443 and "
             f"(ip.DstAddr < {strategies.FRONT_LO_IP} or ip.DstAddr > {strategies.FRONT_HI_IP}))")

    def _filter(self) -> str:
        # ClientHello + голос Discord — всегда; QUIC — когда форс-TCP (по умолчанию да).
        parts = [f"({self._CH})", self._VOICE]
        if self._drop_quic:
            parts.append(self._QUIC)
        return "outbound and ip and (" + " or ".join(parts) + ")"

    def __init__(self, on_log: Optional[Callable[[str], None]] = None) -> None:
        self._on_log = on_log or (lambda _m: None)
        self._w = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._count = 0
        self._strategy = "fakesplit"
        self._targeting = True
        # Форс-TCP: пока движок активен, QUIC (UDP/443) дропается ВСЕГДА. Иначе видео
        # YouTube (googlevideo) и часть трафика Discord/чатов уходят в HTTP/3 мимо
        # TCP-десинка и режутся DPI — страница открывается, а видео/звонок «висят».
        # Дроп только внутри WinDivert-цикла (без правил фаервола) — после стопа/ребута
        # ничего не залипает.
        self._drop_quic = True
        self._targets: set[str] = set()
        # Сколько голосовых пакетов Discord уже продесинкано на каждый dst IP (cutoff).
        self._voice_seen: dict[str, int] = {}

    def set_drop_quic(self, on: bool) -> None:
        self._drop_quic = bool(on)

    @staticmethod
    def available() -> bool:
        return _AVAILABLE

    def is_running(self) -> bool:
        return self._running

    def handled(self) -> int:
        return self._count

    def set_strategy(self, name: str) -> None:
        if name in strategies.STRATEGIES:
            self._strategy = name

    def set_targeting(self, on: bool) -> None:
        self._targeting = bool(on)

    # ------------------------------------------------------------ управление
    def start(self) -> bool:
        if self._running or not _AVAILABLE:
            return False
        # Открываем WinDivert СИНХРОННО — чтобы сразу знать про недоступность
        # (нет прав / конфликт драйвера) и уйти на фолбэк, а не висеть.
        try:
            self._w = pydivert.WinDivert(self._filter())
            self._w.open()
        except Exception as exc:
            self._on_log(f"[engine] WinDivert недоступен: {exc}")
            self._w = None
            return False
        self._targets = hosts.load_targets() if self._targeting else set()
        self._running = True
        self._count = 0
        self._voice_seen = {}
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        try:
            if self._w is not None:
                self._w.close()
        except Exception:
            pass

    # ------------------------------------------------------------ цикл
    def _loop(self) -> None:
        self._on_log(f"[engine] старт — техника «{self._strategy}»"
                     + (" (таргетинг)" if self._targeting else ""))
        try:
            while self._running:
                try:
                    packet = self._w.recv()
                except Exception:
                    break
                # Защитная обёртка: ошибка обработки ОДНОГО пакета не должна ронять
                # весь цикл движка (иначе отвалится и видео, и Discord).
                try:
                    self._handle(packet)
                except Exception:
                    pass
        finally:
            try:
                self._w.close()
            except Exception:
                pass
            self._on_log(f"[engine] стоп (ClientHello обработано: {self._count})")

    def _handle(self, packet) -> None:
        if packet.udp is not None:
            # Голос Discord (UDP к медиасерверам): десинк, реальный пакет всегда проходит.
            if self._is_voice_ip(packet.dst_addr or ""):
                self._handle_voice(packet)
                return
            # Иначе это QUIC (UDP/443, не Discord): дроп при форс-TCP → клиент уходит на TCP.
            if self._drop_quic:
                return
            try:
                self._w.send(packet)
            except Exception:
                pass
            return
        try:
            data = bytes(packet.payload) if packet.payload else b""
            if is_client_hello(data):
                if self._targeting:
                    info = parse_client_hello(data)
                    sni = info.sni if info else ""
                    if sni and not hosts.matches(sni, self._targets):
                        self._w.send(packet)   # не наш хост — пропускаем как есть
                        return
                pos = split_position(data)
                if 0 < pos < len(data):
                    self._apply(packet, data, pos)
                    self._count += 1
                    return
        except Exception:
            pass
        try:
            self._w.send(packet)
        except Exception:
            pass

    # ------------------------------------------------------------ голос Discord (UDP)
    @staticmethod
    def _is_voice_ip(addr: str) -> bool:
        """IP-адрес попадает в диапазон голосовых/медиасерверов Discord (66.22.192.0/18)."""
        if not addr:
            return False
        try:
            v = struct.unpack("!I", socket.inet_aton(addr))[0]
        except OSError:
            return False
        return strategies.VOICE_LO_INT <= v <= strategies.VOICE_HI_INT

    def _handle_voice(self, packet) -> None:
        """Десинк голоса Discord: перед реальным UDP-пакетом шлём несколько фейковых
        STUN с низким TTL (умирают до сервера) — DPI видит «обычный STUN», а не
        Discord-voice, и не душит поток. Реальный пакет форвардим ВСЕГДА без изменений:
        даже если десинк не помог, голос не ломается (нулевой риск регресса). Десинк
        только на первых VOICE_CUTOFF пакетах потока (по dst IP) — DPI решает в начале."""
        key = packet.dst_addr
        seen = self._voice_seen.get(key, 0)
        if seen < strategies.VOICE_CUTOFF:
            self._voice_seen[key] = seen + 1
            for ttl in strategies.FAKE_TTLS:        # авто-TTL: фейк-STUN на разных TTL
                fake = self._clone(packet)
                fake.payload = strategies.build_fake_stun()
                try:
                    fake.ipv4.ttl = ttl
                    fake.ipv4.ident = 0
                except Exception:
                    pass
                try:
                    self._w.send(fake)
                except Exception:
                    pass
        try:
            self._w.send(packet)   # реальный голосовой пакет — без изменений
        except Exception:
            pass

    # ------------------------------------------------------------ техники
    def _clone(self, packet):
        return pydivert.Packet(bytearray(packet.raw), packet.interface, packet.direction)

    def _send_seg(self, p) -> None:
        """Отправка реального сегмента с ip.ident=0 (как ip-id=zero у combined —
        нужно для пробития Google/YouTube-видео)."""
        try:
            p.ipv4.ident = 0
        except Exception:
            pass
        self._w.send(p)

    def _send_fake(self, packet) -> None:
        """Поддельные ClientHello (невинный SNI) ПЕРЕД реальным — отравляют DPI.

        Шлём fake двух типов (покрывают оба вида DPI), и каждый — на нескольких TTL
        («авто-TTL»), чтобы хотя бы один достал DPI на любой дистанции. Ни один fake
        не портит реальное соединение при ЛЮБОМ TTL:
          1) badseq — seq уведён далеко за окно. DPI, инспектирующий пакеты по
             отдельности, видит невинный SNI → пропускает; сервер отбросит как
             out-of-window.
          2) correct-seq + снят ACK (datanoack) — для DPI со сборкой потока по seq:
             fake стоит на месте реального ClientHello, а сервер отбросит сегмент без
             ACK (невалиден в established).
        SNI у каждого fake случайный (анти-fingerprint). Checksum валиден (pydivert
        пересчитает при send).
        """
        seq = packet.tcp.seq_num
        for ttl in strategies.FAKE_TTLS:
            # 1) badseq
            f1 = self._clone(packet)
            f1.payload = strategies.build_fake_clienthello()
            try:
                f1.tcp.seq_num = (seq - 0x40000) & _MASK
                f1.ipv4.ttl = ttl
                f1.ipv4.ident = 0
            except Exception:
                pass
            try:
                self._w.send(f1)
            except Exception:
                pass
            # 2) correct-seq + datanoack
            f2 = self._clone(packet)
            f2.payload = strategies.build_fake_clienthello()
            try:
                f2.tcp.seq_num = seq
                f2.tcp.ack = False
                f2.ipv4.ttl = ttl
                f2.ipv4.ident = 0
            except Exception:
                pass
            try:
                self._w.send(f2)
            except Exception:
                pass

    def _apply(self, packet, data: bytes, pos: int) -> None:
        seq = packet.tcp.seq_num
        st = self._strategy

        if st in ("multidisorder", "multifakedisorder"):
            if st == "multifakedisorder":
                self._send_fake(packet)
            # Несколько точек разрыва: начало записи + середина SNI → 3 сегмента,
            # отправляем в обратном порядке (disorder).
            cuts = sorted({c for c in (3, pos) if 0 < c < len(data)})
            segs, prev = [], 0
            for c in cuts:
                segs.append(data[prev:c]); prev = c
            segs.append(data[prev:])
            pkts, off = [], 0
            for s in segs:
                p = self._clone(packet); p.payload = s
                p.tcp.seq_num = (seq + off) & _MASK; off += len(s)
                pkts.append(p)
            for p in reversed(pkts):
                self._send_seg(p)
            return

        if st in ("split", "disorder", "fakedisorder"):
            if st == "fakedisorder":
                self._send_fake(packet)
            p1 = self._clone(packet); p1.payload = data[:pos]
            p2 = self._clone(packet); p2.payload = data[pos:]
            p2.tcp.seq_num = (seq + pos) & _MASK
            order = (p1, p2) if st == "split" else (p2, p1)  # disorder → обратный порядок
            for p in order:
                self._send_seg(p)
            return

        if st in ("fake", "fakesplit"):
            self._send_fake(packet)
            if st == "fake":
                real = self._clone(packet); real.payload = data
                self._send_seg(real)
            else:  # fakesplit
                p1 = self._clone(packet); p1.payload = data[:pos]
                p2 = self._clone(packet); p2.payload = data[pos:]
                p2.tcp.seq_num = (seq + pos) & _MASK
                self._send_seg(p1); self._send_seg(p2)
            return

        if st == "seqovl":
            ov = strategies.SEQOVL
            seg1 = self._clone(packet)
            seg1.payload = (b"\x00" * ov) + data[:pos]
            seg1.tcp.seq_num = (seq - ov) & _MASK
            seg2 = self._clone(packet)
            seg2.payload = data[pos:]
            seg2.tcp.seq_num = (seq + pos) & _MASK
            self._send_seg(seg1); self._send_seg(seg2)
            return

        # неизвестная техника — пропускаем
        self._w.send(packet)


if __name__ == "__main__":
    import time
    eng = VoidEngine(on_log=print)
    if not VoidEngine.available():
        print("pydivert недоступен")
    elif eng.start():
        print("VoidEngine работает. Ctrl+C — стоп.")
        try:
            while True:
                time.sleep(2); print(f"  split: {eng.handled()}")
        except KeyboardInterrupt:
            eng.stop()
