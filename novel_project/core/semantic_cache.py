"""
语义缓存
========
使用 Embedding 对用户提问做语义相似度匹配，命中则直接返回缓存结果。

原理:
    用户提问 → Embedding（ChromaDB 内置 ONNX 模型，零网络依赖）
             → 向量检索 → 相似度 > 0.85 → 直接返回
                                                                  ↓ 未命中
                                                     走完整 LLM 链路 → 结果入库

使用:
    cache = SemanticCache()
    answer = cache.get("赵玖是什么角色？")  # 命中秒回, 未命中返回 None
    cache.put("赵玖是什么角色？", "赵玖是...", sources=["第一章"])
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from filelock import FileLock

logger = logging.getLogger(__name__)

# ── 配置 ────────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "semantic_cache.json")
SIMILARITY_THRESHOLD = 0.85   # 余弦相似度阈值，> 此值视为命中
MAX_CACHE_ENTRIES = 2000      # 最大缓存条目数

# ── 数据集 ──────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    """单条缓存"""
    query: str                  # 原始问题
    answer: str                 # LLM 生成的回答
    embedding: list[float]      # 问题的 embedding 向量
    sources: list[str] = field(default_factory=list)  # 来源章节
    hit_count: int = 0          # 命中次数
    created_at: float = 0.0     # 创建时间
    last_hit_at: float = 0.0    # 最后命中时间

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "answer": self.answer,
            "embedding": self.embedding,
            "sources": self.sources,
            "hit_count": self.hit_count,
            "created_at": self.created_at,
            "last_hit_at": self.last_hit_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "CacheEntry":
        return CacheEntry(
            query=d["query"],
            answer=d["answer"],
            embedding=d["embedding"],
            sources=d.get("sources", []),
            hit_count=d.get("hit_count", 0),
            created_at=d.get("created_at", 0.0),
            last_hit_at=d.get("last_hit_at", 0.0),
        )


# ── Embedding 工具 ──────────────────────────────────────────────────────

class Embedder:
    """文本向量化。使用 ChromaDB 内置 ONNX 模型（零网络依赖）"""

    _instance = None
    _fn = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def embed(self, text: str) -> list[float]:
        """将文本转为向量"""
        if self._fn is None:
            try:
                import chromadb.utils.embedding_functions as ef
                self._fn = ef.DefaultEmbeddingFunction()
            except Exception as e:
                raise RuntimeError(f"无法加载 ChromaDB embedding: {e}")
        try:
            vec = self._fn([text])[0]
            if hasattr(vec, 'tolist'):
                return vec.tolist()
            return list(vec)
        except Exception:
            # 兜底
            import random
            return [random.gauss(0, 0.1) for _ in range(384)]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """余弦相似度"""
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


# ── 主缓存类 ────────────────────────────────────────────────────────────

class SemanticCache:
    """
    语义缓存。

    并发安全说明：
    命中计数等状态在内存中更新，仅在 put()/clear()/flush() 时落盘；
    落盘与加载通过 FileLock（{cache_file}.lock）做跨进程互斥，
    多进程/多 worker 部署共享同一缓存文件时不会产生截断写。
    注意：读-改-写序列（put 的相似条目查找）仍按单进程内存语义执行，
    多进程下极高频 put 可能互相覆盖对方新增条目（可接受，缓存可重建）。
    """

    def __init__(self, cache_file: str = CACHE_FILE):
        self._file = cache_file
        self._lock = FileLock(self._file + ".lock")
        self._entries: list[CacheEntry] = []
        self._embedder = Embedder.get()
        self._hit_count = 0
        self._miss_count = 0
        self._load()

    # ── 对外接口 ──────────────────────────────────────────────────

    def get(self, query: str) -> str | None:
        """
        语义搜索缓存。
        返回缓存的回答，未命中返回 None。
        """
        entry = self._match(query)
        return entry.answer if entry is not None else None

    def get_with_sources(self, query: str) -> tuple[str | None, list[str]]:
        """返回 (回答, 来源列表)，未命中返回 (None, [])"""
        entry = self._match(query)
        if entry is None:
            return None, []
        return entry.answer, entry.sources

    def _match(self, query: str) -> CacheEntry | None:
        """
        语义匹配最佳条目。
        命中时仅更新内存中的命中计数，不落盘（避免每次命中全量写 MB 级文件）；
        落盘由 put()/clear()/flush() 负责。
        """
        query_vec = self._embedder.embed(query)
        best_score = 0.0
        best_entry: CacheEntry | None = None

        for entry in self._entries:
            score = Embedder.cosine_similarity(query_vec, entry.embedding)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score >= SIMILARITY_THRESHOLD and best_entry:
            best_entry.hit_count += 1
            best_entry.last_hit_at = time.time()
            self._hit_count += 1
            return best_entry

        self._miss_count += 1
        return None

    def put(self, query: str, answer: str, sources: list[str] | None = None):
        """
        写入缓存。
        如果语义相似条目已存在则更新，否则新增。
        """
        query_vec = self._embedder.embed(query)

        # 查找是否已有相似条目
        for entry in self._entries:
            score = Embedder.cosine_similarity(query_vec, entry.embedding)
            if score >= SIMILARITY_THRESHOLD:
                # 更新已有条目
                entry.answer = answer
                if sources:
                    entry.sources = list(set(entry.sources + sources))
                entry.hit_count += 1
                entry.last_hit_at = time.time()
                self._save()
                return

        # 新增
        entry = CacheEntry(
            query=query,
            answer=answer,
            embedding=query_vec,
            sources=sources or [],
            hit_count=1,
            created_at=time.time(),
            last_hit_at=time.time(),
        )
        self._entries.append(entry)

        # 超限淘汰：按命中次数排序，淘汰最少命中的
        if len(self._entries) > MAX_CACHE_ENTRIES:
            self._entries.sort(key=lambda e: (e.hit_count, e.last_hit_at))
            self._entries = self._entries[-MAX_CACHE_ENTRIES:]

        self._save()

    def stats(self) -> dict:
        """缓存统计"""
        total = self._hit_count + self._miss_count
        hit_rate = self._hit_count / max(total, 1) * 100
        return {
            "entries": len(self._entries),
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "total_requests": total,
            "hit_rate": round(hit_rate, 1),
            "threshold": SIMILARITY_THRESHOLD,
            "top_queries": sorted(
                [{"query": e.query[:30], "hits": e.hit_count} for e in self._entries],
                key=lambda x: -x["hits"],
            )[:10],
        }

    def clear(self):
        """清空缓存"""
        self._entries = []
        self._hit_count = 0
        self._miss_count = 0
        self._save()

    def flush(self):
        """显式落盘：将内存中的命中计数等状态写入磁盘（get() 命中不再自动落盘）"""
        self._save()

    # ── 持久化 ────────────────────────────────────────────────────

    def _load(self):
        """从磁盘加载缓存（FileLock 跨进程互斥）"""
        if not os.path.exists(self._file):
            return
        try:
            with self._lock:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            self._entries = [CacheEntry.from_dict(e) for e in data.get("entries", [])]
            self._hit_count = data.get("hit_count", 0)
            self._miss_count = data.get("miss_count", 0)
        except Exception:
            self._entries = []

    def _save(self):
        """保存到磁盘（FileLock 跨进程互斥 + 原子写：临时文件 + os.replace）"""
        cache_dir = os.path.dirname(self._file)
        os.makedirs(cache_dir, exist_ok=True)
        data = {
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "entries": [e.to_dict() for e in self._entries],
        }
        with self._lock:
            fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=cache_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._file)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise


# ── 全局单例 ────────────────────────────────────────────────────────────

_cache: SemanticCache | None = None


def get_cache() -> SemanticCache:
    global _cache
    if _cache is None:
        _cache = SemanticCache()
    return _cache


# ── 集成到问答链路的中间件函数 ─────────────────────────────────────────

def cached_ask(query: str, llm_func: Callable) -> str:
    """
    缓存感知的问答函数。

    使用:
        answer = cached_ask("赵玖是谁？", lambda: coordinator.run("赵玖是谁？"))
    """
    cache = get_cache()

    # 查缓存
    cached = cache.get(query)
    if cached is not None:
        logger.info(f"  [SemanticCache] ✅ 缓存命中: {query[:30]}...")
        return cached

    # 未命中，走 LLM
    logger.warning(f"  [SemanticCache] ❌ 缓存未命中: {query[:30]}...")
    answer = llm_func()
    if answer:
        cache.put(query, answer)
    return answer


# ── 测试 ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cache = SemanticCache()
    # 写入测试
    cache.put("赵玖是什么角色？", "赵玖是小说的主角，穿越为宋高宗赵构。", ["全书概要"])
    # 读取测试（完全匹配）
    result = cache.get("赵玖是什么角色？")
    print(f"完全匹配: {result is not None}")
    # 读取测试（语义相似）
    result = cache.get("赵玖是谁？")
    print(f"语义匹配: {result is not None}")
    print(json.dumps(cache.stats(), ensure_ascii=False, indent=2))
