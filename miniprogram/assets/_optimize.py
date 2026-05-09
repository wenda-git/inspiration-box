#!/usr/bin/env python3
"""
二次优化：对实际可以牺牲一些质量的图进一步减小体积。
策略：
  - fruits: PNG → 量化到 128 色 palette PNG（透明保留）
  - illustrations/bg: 同上
  - brand: logo 保留更高质量
"""
from pathlib import Path
from PIL import Image

TARGETS = [
    ("fruits", 96),
    ("illustrations", 128),
    ("bg", 64),
]


def quantize_png(src: Path, colors: int) -> None:
    img = Image.open(src).convert("RGBA")
    orig = src.stat().st_size

    # 如果有透明，保留 alpha
    alpha = img.split()[-1]
    # 量化：使用 libimagequant 失败时 fallback 到 median cut
    try:
        quantized = img.quantize(colors=colors, method=Image.Quantize.LIBIMAGEQUANT)
    except Exception:
        quantized = img.quantize(colors=colors)

    quantized.save(src, optimize=True)
    new = src.stat().st_size
    print(f"  {src.name:40} {orig // 1024}KB → {new // 1024}KB")


def main():
    base = Path(__file__).resolve().parent
    for sub, colors in TARGETS:
        d = base / sub
        print(f"\n== {sub} (quantize to {colors} colors) ==")
        for p in sorted(d.glob("*.png")):
            quantize_png(p, colors)


if __name__ == "__main__":
    main()
