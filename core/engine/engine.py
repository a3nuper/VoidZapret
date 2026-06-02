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
    FILTER = "outbound and ip and tcp.PayloadLength > 0 and tcp.DstPort == 443"

    def __init__(self, on_log: Optional[Callable[[str], None]] = None) -> None:
        self._on_log = on_log or (lambda _m: None)
        self._w = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._count = 0
        self._strategy = "fakesplit"
        self._targeting = True
        self._targets: set[str] = set()

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
            self._w = pydivert.WinDivert(self.FILTER)
            self._w.open()
        except Exception as exc:
            self._on_log(f"[engine] WinDivert недоступен: {exc}")
            self._w = None
            return False
        self._targets = hosts.load_targets() if self._targeting else set()
        self._running = True
        self._count = 0
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
                self._handle(packet)
        finally:
            try:
                self._w.close()
            except Exception:
                pass
            self._on_log(f"[engine] стоп (ClientHello обработано: {self._count})")

    def _handle(self, packet) -> None:
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
        """Поддельный ClientHello (невинный SNI, битый checksum + низкий TTL)."""
        fake = self._clone(packet)
        fake.payload = strategies.build_fake_clienthello()
        try:
            fake.ipv4.ttl = strategies.FAKE_TTL
            fake.ipv4.ident = 0
        except Exception:
            pass
        self._w.send(fake, recalculate_checksum=False)  # битый checksum → сервер дропнет

    def _apply(self, packet, data: bytes, pos: int) -> None:
        seq = packet.tcp.seq_num
        st = self._strategy

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
