#!/usr/bin/env python3
"""
从 raw/compress_backup 和 raw/tabbar_backup 恢复原图，
然后用"保留完整 RGBA + PIL 极限优化"的方式重压，不走 quantize 以免丢失半透明。

策略：
  - 等比缩放到目标最大边
  - 保留 RGBA，用 PIL optimize + compress_level=9 保存
  - 目标：每张 < 200KB，透明背景完整保留
"""
import shutil
from pathlib import Path
from PIL import Image

# target_max_side
RULES = {
    "fruits":        420,   # 水果产品图稍小
    "brand":         480,
    "illustrations": 720,
    "bg":            480,
}


def recompress(src: Path, backup: Path, max_side: int) -> None:
    if not backup.exists():
        print(f"  [skip] 没备份: {src.name}")
        return
    shutil.copy2(backup, src)

    img = Image.open(src).convert("RGBA")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        new = (int(w * scale), int(h * scale))
        img = img.resize(new, Image.LANCZOS)

    img.save(src, optimize=True, compress_level=9)
    size = src.stat().st_size
    print(f"  {src.name:36} {img.size[0]}×{img.size[1]}  {size // 1024}KB")


def main():
    base = Path(__file__).resolve().parent
    backup_dir = base / "raw" / "compress_backup"

    for sub, max_side in RULES.items():
        d = base / sub
        print(f"\n== {sub} (max {max_side}px) ==")
        for p in sorted(d.glob("*.png")):
            backup = backup_dir / f"{sub}_{p.name}"
            recompress(p, backup, max_side)


if __name__ == "__main__":
    main()
