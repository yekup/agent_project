"""
向量召回率评估
==============
对黄金测试集中的实体类问题（main_character / character_relation），
测量检索 top-k 的分块中是否出现目标实体，输出 recall@1/3/5。

用法:
    cd novel_project
    python scripts/vector_recall_eval.py                 # hybrid：retriever 混合检索
    python scripts/vector_recall_eval.py --mode vector   # 纯向量基线
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

GOLDEN_PATH = os.path.join(BASE_DIR, "data", "eval", "golden", "shaosong.json")
WIKI_PATH = os.path.join(BASE_DIR, "data", "wiki", "绍宋作者：榴弹怕水_hierarchical.json")
GRAPH_PATH = os.path.join(BASE_DIR, "data", "wiki", "绍宋作者：榴弹怕水_graph.json")
NOVEL_PATH = os.path.join(BASE_DIR, "data", "processed", "《绍宋》作者：榴弹怕水.json")


def _entities_of(item: dict) -> tuple[list[str], str] | None:
    """返回 (目标实体列表, 命中模式)。mode: 'any'（任一别名命中）/ 'all'（全部命中）"""
    md = item.get("metadata", {})
    qtype = md.get("type")
    if qtype == "character_relation":
        return [md["character"], md["target"]], "all"
    if qtype == "main_character":
        # 「赵玖（赵构） 是《绍宋》中的什么角色？」→ [赵玖, 赵构]
        name = re.split(r"\s*是《", item["query"])[0]
        parts = [p.strip() for p in re.split(r"[（()]", name) if p.strip()]
        return (parts, "any") if parts else None
    return None


def _hit(entities: list[str], mode: str, hits: list[dict], k: int) -> bool:
    corpus = ""
    for h in hits[:k]:
        corpus += h.get("text", "") + "\n"
        corpus += h.get("metadata", {}).get("chapter_title", "") + "\n"
    present = [e in corpus for e in entities]
    return any(present) if mode == "any" else all(present)


def main():
    parser = argparse.ArgumentParser(description="向量召回率评估")
    parser.add_argument("--mode", choices=["vector", "hybrid"], default="hybrid")
    parser.add_argument("--golden", default=GOLDEN_PATH)
    args = parser.parse_args()

    with open(args.golden, "r", encoding="utf-8") as f:
        golden = json.load(f)

    if args.mode == "vector":
        from core.chunker import VectorStoreIndexer
        indexer = VectorStoreIndexer()
        search_fn = lambda q, k: indexer.search(q, top_k=k)  # noqa: E731
    else:
        from core.retriever import NovelRetriever
        retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
        search_fn = lambda q, k: retriever.search_by_vector(q, top_k=k)  # noqa: E731

    ks = (1, 3, 5)
    rows = []
    for item in golden:
        parsed = _entities_of(item)
        if not parsed:
            continue
        entities, mode = parsed
        hits = search_fn(item["query"], max(ks))
        recalls = {k: _hit(entities, mode, hits, k) for k in ks}
        rows.append((item["query"], item["metadata"]["type"], recalls))

    print("=" * 78)
    print(f"mode={args.mode}  entity queries={len(rows)}")
    print("=" * 78)
    print(f"{'Query':<46}{'Type':<20}{'k=1':>5}{'k=3':>5}{'k=5':>5}")
    print("-" * 78)
    for query, qtype, recalls in rows:
        marks = "".join(f"{1 if recalls[k] else 0:>5}" for k in ks)
        print(f"{query[:44]:<46}{qtype:<20}{marks}")
    print("-" * 78)
    for k in ks:
        avg = sum(1 for _, _, r in rows if r[k]) / max(len(rows), 1)
        print(f"recall@{k}: {avg:.1%}")


if __name__ == "__main__":
    main()
