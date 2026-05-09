#!/usr/bin/env python3
"""
裁掉 PNG 四周的透明边，保留一点呼吸空间。
用法：python3 _trim.py <file1.png> [file2.png] ...
原图备份到 raw/trimmed_backup/
"""
import sys
import shutil
from pathlib import Path
from PIL import Image

PADDING = 16  # 裁完四周留 16px 透明缓冲，避免贴得太满

def trim(path: Path, backup_dir: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    img = Image.open(path).convert("RGBA")
    orig_size = img.size

    # 用 alpha 通道计算非透明边界
    bbox = img.getbbox()
    if bbox is None:
        return orig_size, orig_size

    left, top, right, bottom = bbox
    # 加 padding，同时保持不超出原图
    left = max(0, left - PADDING)
    top = max(0, top - PADDING)
    right = min(img.width, right + PADDING)
    bottom = min(img.height, bottom + PADDING)

    cropped = img.crop((left, top, right, bottom))
    new_size = cropped.size

    # 备份原图
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    if not backup_path.exists():
        shutil.copy2(path, backup_path)

    # 覆盖原图
    cropped.save(path, optimize=True)
    return orig_size, new_size


def main():
    if len(sys.argv) < 2:
        print("用法：python3 _trim.py <file.png> ...")
        sys.exit(1)

    base = Path(__file__).resolve().parent
    backup_dir = base / "raw" / "trimmed_backup"

    for arg in sys.argv[1:]:
        p = Path(arg).resolve()
        if not p.exists():
            print(f"[跳过] 不存在: {p}")
            continue
        orig, new = trim(p, backup_dir)
        saved = (1 - (new[0] * new[1]) / (orig[0] * orig[1])) * 100
        print(f"[OK] {p.name}  {orig[0]}×{orig[1]}  →  {new[0]}×{new[1]}  (省 {saved:.0f}%)")


if __name__ == "__main__":
    main()
