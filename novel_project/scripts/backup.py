"""
数据备份脚本
==========
备份 data/ 下的关键目录，打包为带时间戳的压缩文件。

用法:
    python scripts/backup.py                        # 全量备份
    python scripts/backup.py --output D:/backups     # 指定输出目录
    python scripts/backup.py --no-wiki               # 排除 wiki 目录
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 需要备份的目录/文件模式
BACKUP_ITEMS = [
    "data/processed",
    "data/wiki",
    "data/vocab",
    "data/chroma",
    "data/checkpoints",
    "data/eval",
    "data/users.json",
]

# 排除模式
EXCLUDE_PATTERNS = ["*.bak.*", "__pycache__", ".git"]


def should_exclude(name: str) -> bool:
    for pat in EXCLUDE_PATTERNS:
        if pat.startswith("*") and pat[1:] in name:
            return True
        if name == pat:
            return True
    return False


def run_backup(output_dir: str, no_wiki: bool = False) -> str:
    """执行备份，返回备份文件路径"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"novel_graphrag_backup_{ts}.tar.gz"
    os.makedirs(output_dir, exist_ok=True)
    backup_path = os.path.join(output_dir, backup_name)

    items = [i for i in BACKUP_ITEMS if not no_wiki or "wiki" not in i]
    existing_items = [i for i in items if (BASE_DIR / i).exists()]

    if not existing_items:
        print("没有找到任何需要备份的数据目录。")
        return ""

    # 写入备份元数据
    manifest = {
        "backup_time": ts,
        "items": existing_items,
        "sizes": {},
    }
    for item in existing_items:
        target = BASE_DIR / item
        if target.is_file():
            manifest["sizes"][item] = target.stat().st_size
        elif target.is_dir():
            total = sum(
                f.stat().st_size for f in target.rglob("*") if f.is_file()
            )
            manifest["sizes"][item] = total

    # 打包
    with tarfile.open(backup_path, "w:gz") as tar:
        # 写入清单
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = datetime.now().timestamp()
        tar.addfile(info, io.BytesIO(manifest_bytes))

        # 写入数据
        for item in existing_items:
            target = BASE_DIR / item
            arcname = f"data/{Path(item).relative_to('data')}" if "data/" in item else item
            if target.is_file():
                tar.add(str(target), arcname=arcname)
            elif target.is_dir():
                tar.add(str(target), arcname=arcname)

    size_mb = os.path.getsize(backup_path) / 1024 / 1024
    print(f"备份完成: {backup_path}")
    print(f"  包含: {len(existing_items)} 个目录/文件")
    print(f"  大小: {size_mb:.2f} MB")
    print(f"  清单: {json.dumps(manifest['sizes'], ensure_ascii=False)}")
    return backup_path


def main():
    parser = argparse.ArgumentParser(description="Novel-GraphRAG 数据备份")
    parser.add_argument("--output", default=str(BASE_DIR / "backups"), help="备份输出目录")
    parser.add_argument("--no-wiki", action="store_true", help="排除 wiki 目录")
    args = parser.parse_args()

    run_backup(args.output, no_wiki=args.no_wiki)


if __name__ == "__main__":
    main()
