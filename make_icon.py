"""Конвертация логотипа (PNG) в многоразмерный icon.ico для exe и установщика.

Использование:  py make_icon.py [путь_к_png]
По умолчанию берёт logo.png рядом со скриптом и пишет icon.ico туда же.
"""

import sys
from pathlib import Path

from PIL import Image

SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def main() -> int:
    here = Path(__file__).resolve().parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "logo.png"
    if not src.is_file():
        print(f"[!] Не найден файл логотипа: {src}")
        print("    Сохрани картинку как logo.png в папку zapret-gui и запусти снова.")
        return 1

    img = Image.open(src).convert("RGBA")
    # Приводим к квадрату по большей стороне (на случай не-квадратного PNG).
    w, h = img.size
    if w != h:
        side = max(w, h)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
        img = canvas

    out = here / "icon.ico"
    img.save(out, format="ICO", sizes=SIZES)
    print(f"[OK] icon.ico создан: {out}  (размеры: {', '.join(str(s[0]) for s in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
