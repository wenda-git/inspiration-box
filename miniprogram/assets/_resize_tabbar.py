#!/usr/bin/env python3
"""
把 tabbar 图标压到 81×81 px（微信 TabBar 规定尺寸）+ 透明 + 优化。
原图备份到 raw/tabbar_backup/
"""
import sys
import shutil
from pathlib import Path
from PIL import Image

TARGET = 81  # 微信 tabBar 规范

def process(src: Path, backup_dir: Path):
    img = Image.open(src).convert("RGBA")
    orig_size = img.size
    orig_bytes = src.stat().st_size

    # 先按非透明区域 crop
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    # 正方形画布 + 等比缩放填满
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((TARGET, TARGET), Image.LANCZOS)

    # 备份
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / src.name
    if not backup.exists():
        shutil.copy2(src, backup)

    # 用尽可能高的压缩保存
    canvas.save(src, optimize=True, compress_level=9)

    new_bytes = src.stat().st_size
    print(f"[OK] {src.name}  {orig_size[0]}×{orig_size[1]} → {TARGET}×{TARGET}  "
          f"{orig_bytes // 1024}KB → {new_bytes // 1024}KB")


def main():
    base = Path(__file__).resolve().parent
    tabbar_dir = base / "tabbar"
    backup_dir = base / "raw" / "tabbar_backup"

    if not tabbar_dir.exists():
        print("❌ 没找到 tabbar/")
        sys.exit(1)

    for p in sorted(tabbar_dir.glob("*.png")):
        process(p, backup_dir)


if __name__ == "__main__":
    main()
