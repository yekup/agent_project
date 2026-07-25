"""
跨书全文检索
============
建立简单倒排索引，支持多书共查。

索引结构:
    inverted_index.json
    {
        "keyword": [
            {"book": "绍宋", "chapter_index": 12, "chapter_title": "第十二章", "score": 1},
            ...
        ]
    }

使用:
    index = MultiBookIndex()
    results = index.search("赵玖 岳飞", top_k=20)
">
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_DIR = BASE_DIR / "data" / "index"
INDEX_FILE = INDEX_DIR / "inverted_index.json"
WIKI_DIR = BASE_DIR / "data" / "wiki"


class MultiBookIndex:
    """跨书倒排索引"""

    def __init__(self, rebuild: bool = False):
        self._index: dict[str, list[dict]] = {}
        self._book_meta: dict[str, dict] = {}
        if rebuild or not INDEX_FILE.exists():
            self.build()
        else:
            self.load()

    # ── 对外接口 ──────────────────────────────────────────────────

    def search(
        self, query: str, top_k: int = 20, books: list[str] | None = None
    ) -> list[dict]:
        """
        跨书全文检索。

        Args:
            query: 搜索关键词（多个词用空格分隔）
            top_k: 返回条数
            books: 限定搜索的书目，None 表示全部

        Returns:
            [{"book": "绍宋", "chapter_index": 12, "chapter_title": "...",
              "summary": "...", "score": 2}, ...]
        """
        # 拆词
        words = [w.strip() for w in re.split(r"[\s,，、]+", query) if len(w.strip()) >= 2]
        if not words:
            return []

        # 合并所有词匹配的结果，按出现次数算分
        score_map: dict[tuple[str, int], dict] = {}

        for word in words:
            word_lower = word.lower()
            entries = self._index.get(word_lower, [])

            for entry in entries:
                if books and entry.get("book") not in books:
                    continue
                key = (entry["book"], entry["chapter_index"])
                if key not in score_map:
                    score_map[key] = {**entry, "score": 0}
                score_map[key]["score"] += 1

        # 排序输出
        results = sorted(score_map.values(), key=lambda x: -x["score"])
        return results[:top_k]

    def search_by_book(
        self, query: str, book: str, top_k: int = 10
    ) -> list[dict]:
        """在单本书中检索"""
        return self.search(query, top_k=top_k, books=[book])

    def get_books(self) -> list[str]:
        """获取索引中所有书籍"""
        books = set()
        for entries in self._index.values():
            for e in entries:
                books.add(e["book"])
        return sorted(books)

    def book_stats(self) -> list[dict]:
        """每本书的统计信息"""
        stats: dict[str, set] = defaultdict(set)
        for entries in self._index.values():
            for e in entries:
                stats[e["book"]].add(e["chapter_index"])
        return [
            {
                "book": book,
                "chapters": len(chapters),
                "display_name": _display_name(book),
            }
            for book, chapters in sorted(stats.items())
        ]

    # ── 索引构建 ──────────────────────────────────────────────────

    def build(self):
        """从所有 Wiki 数据重建索引"""
        logger.info(f"[MultiBookIndex] 构建跨书索引...")
        index: dict[str, list[dict]] = defaultdict(list)

        for wiki_path in sorted(WIKI_DIR.glob("*_hierarchical.json")):
            book_key = _book_key(wiki_path)
            if not book_key:
                continue

            try:
                with open(wiki_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"  跳过 {wiki_path.name}: {e}")
                continue

            chapters = data.get("chapters", [])
            if isinstance(data, list):
                chapters = data

            for ch in chapters:
                title = ch.get("chapter_title") or ch.get("title", "")
                summary = ch.get("summary", "") or ""
                idx = ch.get("chapter_index", 0)
                chars = " ".join(
                    [c["name"] for c in ch.get("characters", [])[:5]]
                )
                events = " ".join(ch.get("events", [])[:3])

                # 从标题/摘要/人物/事件中提取关键词
                text = f"{title} {summary} {chars} {events}"
                words = self._extract_keywords(text)

                for word in words:
                    entry = {
                        "book": book_key,
                        "chapter_index": idx,
                        "chapter_title": title,
                        "summary": summary[:150],
                    }
                    if entry not in index[word]:
                        index[word].append(entry)

            logger.info(f"  索引完成: {book_key} ({len(chapters)} 章)")

        self._index = dict(index)
        self.save()
        logger.info(f"[MultiBookIndex] 索引构建完成: {len(self._index)} 关键词")

    def save(self):
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=1)
        logger.info(f"[MultiBookIndex] 索引已保存: {INDEX_FILE}")

    def load(self):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            self._index = json.load(f)
        logger.info(f"[MultiBookIndex] 索引已加载: {len(self._index)} 关键词")

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从文本中提取关键词（中文分词后去重）"""
        # 简单分词：按单字/双字切分
        text_clean = re.sub(r"[^一-鿿\w]", "", text)
        words = set()

        # 2-4 字词
        for i in range(len(text_clean)):
            for l in range(2, 5):
                if i + l <= len(text_clean):
                    words.add(text_clean[i : i + l].lower())

        # 人名（通过 character 识别）
        return list(words)


def _book_key(path: Path) -> str:
    """从文件名提取书 key"""
    name = path.name.replace("_hierarchical.json", "")
    if not name or name == "test":
        return ""
    return name


def _display_name(key: str) -> str:
    """显示名称"""
    names = {
        "绍宋作者：榴弹怕水": "《绍宋》",
        "斗破苍穹作者：天蚕土豆": "《斗破苍穹》",
        "神印王座作者：唐家三少": "《神印王座》",
    }
    return names.get(key, key)


# ── 搜索 API ────────────────────────────────────────────────────────────

_index_instance: MultiBookIndex | None = None


def get_index(rebuild: bool = False) -> MultiBookIndex:
    global _index_instance
    if _index_instance is None or rebuild:
        _index_instance = MultiBookIndex(rebuild=rebuild)
    return _index_instance
