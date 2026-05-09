#!/usr/bin/env python3
"""
brand 目录的 logo 大部分是简单插画，用 palette PNG 可以更狠地压。
"""
from pathlib import Path
from PIL import Image

base = Path(__file__).resolve().parent
for p in sorted((base / "brand").glob("*.png")):
    img = Image.open(p).convert("RGBA")
    orig = p.stat().st_size
    try:
        q = img.quantize(colors=64, method=Image.Quantize.LIBIMAGEQUANT)
    except Exception:
        q = img.quantize(colors=64)
    q.save(p, optimize=True)
    new = p.stat().st_size
    print(f"{p.name:30} {orig // 1024}KB → {new // 1024}KB")
