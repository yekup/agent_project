"""
书籍注册表（零登记多书支持）
============================
扫描 data/wiki/ 下的编译产物自动发现书籍，派生显示名与向量索引 key。
新增书籍无需修改任何代码——上传编译完成后自动出现在书籍列表中。

约定:
- 书籍唯一 id = wiki 文件名前缀（如 "绍宋作者：榴弹怕水"）
- 显示名 = 去掉「《》」与「作者：xxx」后缀（"《绍宋》作者：榴弹怕水" → "绍宋"）
- 向量库 novel_key：历史三书沿用短名（兼容已建索引），新书直接用全名——
  索引与检索都经 vector_key_for() 解析，两侧天然一致
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI_DIR = os.path.join(BASE_DIR, "data", "wiki")
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")

# 历史向量索引的短名映射（仅兼容已建索引的三本书；新书无需在此登记，
# 未命中时 vector_key 直接回退为全名）
LEGACY_VECTOR_KEYS = {
    "绍宋作者：榴弹怕水": "shaosong",
    "斗破苍穹作者：天蚕土豆": "doupo",
    "神印王座作者：唐家三少": "shenyin",
}


@dataclass
class Book:
    name: str           # wiki 全名（唯一 id）
    display_name: str   # 展示名
    vector_key: str     # 向量库 novel_key
    has_graph: bool
    has_wiki: bool


def display_name_for(name: str) -> str:
    """"斗破苍穹作者：天蚕土豆" / "《绍宋》作者：榴弹怕水" → 书名核心词"""
    core = name.split("作者：")[0].strip().strip("《》")
    return core or name


def vector_key_for(name: str) -> str:
    """wiki 全名 → 向量库 novel_key（短名映射未命中则回退全名）"""
    return LEGACY_VECTOR_KEYS.get(name, name)


def resolve_name(name_or_short: str) -> str:
    """短名/显示名/wiki 全名 → wiki 全名"""
    for full, short in LEGACY_VECTOR_KEYS.items():
        if name_or_short == short:
            return full
    return name_or_short


def processed_json_for(name_or_short: str) -> str:
    """
    解析 data/processed/ 下的原文 JSON 路径。
    按书名核心词 glob（兼容「《绍宋》作者：xx.json」的书名号），
    找不到时返回约定路径（调用方负责检查存在性）。
    """
    name = resolve_name(name_or_short)
    core = display_name_for(name)
    for p in sorted(glob.glob(os.path.join(PROCESSED_DIR, f"*{core}*.json"))):
        if not p.endswith("_chunks.json"):
            return p
    return os.path.join(PROCESSED_DIR, f"{name}.json")


def list_books(wiki_dir: str | None = None) -> list[Book]:
    """扫描 wiki 目录，返回所有已编译书籍（有图谱或有 wiki 即视为已编译）"""
    wiki_dir = wiki_dir or WIKI_DIR
    names: set[str] = set()
    for gf in glob.glob(os.path.join(wiki_dir, "*_graph.json")):
        n = os.path.basename(gf)[: -len("_graph.json")]
        if n != "test":
            names.add(n)
    for hf in glob.glob(os.path.join(wiki_dir, "*_hierarchical.json")):
        n = os.path.basename(hf)[: -len("_hierarchical.json")]
        if n != "test":
            names.add(n)

    return [
        Book(
            name=n,
            display_name=display_name_for(n),
            vector_key=vector_key_for(n),
            has_graph=os.path.exists(os.path.join(wiki_dir, f"{n}_graph.json")),
            has_wiki=os.path.exists(os.path.join(wiki_dir, f"{n}_hierarchical.json")),
        )
        for n in sorted(names)
    ]
