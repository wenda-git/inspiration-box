#!/usr/bin/env python3
"""
批量压缩所有资源图到小程序可接受的大小。
- fruits/ 水果产品图 → 480×480, 目标 <150KB
- brand/ Logo → 最长边 600px, <150KB
- illustrations/ 插画 → 最长边 900px, <200KB
- bg/ 装饰 → 最长边 600px, <120KB
原图备份到 raw/compress_backup/
"""
import shutil
from pathlib import Path
from PIL import Image

RULES = {
    "fruits":        {"max_side": 480, "quality": 82},
    "brand":         {"max_side": 600, "quality": 85},
    "illustrations": {"max_side": 900, "quality": 82},
    "bg":            {"max_side": 600, "quality": 78},
}


def compress_png(src: Path, max_side: int, quality: int, backup_dir: Path) -> None:
    img = Image.open(src).convert("RGBA")
    orig_size = img.size
    orig_bytes = src.stat().st_size

    # 等比缩放
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        new = (int(w * scale), int(h * scale))
        img = img.resize(new, Image.LANCZOS)

    # 备份
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{src.parent.name}_{src.name}"
    if not backup.exists():
        shutil.copy2(src, backup)

    # PNG 优化压缩
    img.save(src, optimize=True, compress_level=9)

    new_bytes = src.stat().st_size
    print(f"[{src.parent.name:14}] {src.name:36}  "
          f"{orig_size[0]}×{orig_size[1]} → {img.size[0]}×{img.size[1]}  "
          f"{orig_bytes // 1024}KB → {new_bytes // 1024}KB")


def main():
    base = Path(__file__).resolve().parent
    backup_dir = base / "raw" / "compress_backup"

    for sub, conf in RULES.items():
        d = base / sub
        if not d.exists():
            continue
        for p in sorted(d.glob("*.png")):
            compress_png(p, conf["max_side"], conf["quality"], backup_dir)


if __name__ == "__main__":
    main()
