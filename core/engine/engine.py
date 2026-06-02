"""VoidEngine — независимый движок обхода DPI на WinDivert (pydivert).

MVP-техника: перехватываем исходящий TLS ClientHello (TCP/443) и пересобираем
его в ДВА TCP-сегмента с разрывом внутри SNI. DPI, читающий SNI одним куском,
не находит сигнатуру → соединение проходит. Это первая, базовая техника нашего
собственного метода (как ядро GoodbyeDPI); далее добавим fake-пакеты, seqovl,
авто-калибровку и таргетинг по хостам.

Требует прав администратора (WinDivert-драйвер). Запуск отдельно от winws.
"""

import threading
from typing import Callable, Optional

from core.engine.tls import is_client_hello, split_position

try:
    import pydivert
    _AVAILABLE = True
except Exception:
    pydivert = None
    _AVAILABLE = False


class VoidEngine:
    """Свой обход: split ClientHello на исходящем TCP/443."""

    FILTER = "outbound and ip and tcp.PayloadLength > 0 and tcp.DstPort == 443"

    def __init__(self, on_log: Optional[Callable[[str], None]] = None) -> None:
        self._on_log = on_log or (lambda _m: None)
        self._w = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._count = 0

    @staticmethod
    def available() -> bool:
        return _AVAILABLE

    def is_running(self) -> bool:
        return self._running

    def handled(self) -> int:
        return self._count

    # ------------------------------------------------------------ управление
    def start(self) -> bool:
        if self._running or not _AVAILABLE:
            return False
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
        try:
            self._w = pydivert.WinDivert(self.FILTER)
            self._w.open()
        except Exception as exc:
            self._on_log(f"[engine] не удалось открыть WinDivert: {exc}")
            self._running = False
            return
        self._on_log("[engine] запущен — split ClientHello на TCP/443")
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
            self._on_log(f"[engine] остановлен (ClientHello обработано: {self._count})")

    def _handle(self, packet) -> None:
        try:
            data = bytes(packet.payload) if packet.payload else b""
            if is_client_hello(data):
                pos = split_position(data)
                if 0 < pos < len(data):
                    self._send_split(packet, data, pos)
                    self._count += 1
                    return
        except Exception:
            pass
        # всё прочее — пропускаем как есть
        try:
            self._w.send(packet)
        except Exception:
            pass

    def _send_split(self, packet, data: bytes, pos: int) -> None:
        """Делит ClientHello на два сегмента (seq второго смещён на pos)."""
        p1 = pydivert.Packet(bytearray(packet.raw), packet.interface, packet.direction)
        p1.payload = data[:pos]
        p2 = pydivert.Packet(bytearray(packet.raw), packet.interface, packet.direction)
        p2.payload = data[pos:]
        p2.tcp.seq_num = (packet.tcp.seq_num + pos) & 0xFFFFFFFF
        self._w.send(p1)
        self._w.send(p2)


if __name__ == "__main__":
    # Ручной тест (нужны права администратора): py -m core.engine.engine
    import time
    eng = VoidEngine(on_log=print)
    if not VoidEngine.available():
        print("pydivert недоступен")
    elif eng.start():
        print("VoidEngine работает. Ctrl+C для остановки.")
        try:
            while True:
                time.sleep(2)
                print(f"  ClientHello split: {eng.handled()}")
        except KeyboardInterrupt:
            eng.stop()
