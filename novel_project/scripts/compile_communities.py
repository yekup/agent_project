"""
社区检测与摘要编译 CLI
======================
对指定小说（或全部已编译小说）运行人物社群检测并生成摘要，
结果追加到 data/wiki/{novel}_graph.json 的 communities 字段。

图谱重编（save_graph 覆盖）后社区数据会丢失，用本脚本补跑即可；
传 --cached 可复用成员集未变的旧摘要（省 LLM 调用）。

用法:
    cd novel_project
    python scripts/compile_communities.py                # 全部已编译小说
    python scripts/compile_communities.py 绍宋作者：榴弹怕水
    python scripts/compile_communities.py --cached       # 复用旧摘要
"""
from __future__ import annotations

import glob
import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.knowledge_graph import load_graph  # noqa: E402
from core.graph_community import (  # noqa: E402
    detect_communities,
    generate_community_summaries,
    load_community_data,
    save_community_data,
)

logger = logging.getLogger(__name__)


def compile_one(graph_path: str, use_cached: bool = False) -> None:
    name = os.path.basename(graph_path).replace("_graph.json", "")
    novel = name.split("作者：")[0]  # 小说名（用于 LLM prompt 上下文）

    G = load_graph(graph_path)
    cached = None
    if use_cached:
        cached = (load_community_data(graph_path) or {}).get("summaries")

    communities = detect_communities(G)
    summaries = generate_community_summaries(
        G, communities, novel=novel, cached_summaries=cached)
    save_community_data(communities, summaries, graph_path)
    llm_count = sum(1 for s in summaries if len(s["summary"]) > 50)
    print(f"{name}: {len(summaries)} 个社区（{llm_count} 个 LLM 摘要）")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_cached = "--cached" in sys.argv

    if args:
        targets = [os.path.join(BASE_DIR, "data", "wiki", f"{a}_graph.json") for a in args]
    else:
        targets = sorted(glob.glob(os.path.join(BASE_DIR, "data", "wiki", "*_graph.json")))

    for path in targets:
        if not os.path.exists(path):
            print(f"跳过（图谱不存在）: {path}")
            continue
        try:
            compile_one(path, use_cached=use_cached)
        except Exception:
            logger.exception("社区编译失败: %s", path)


if __name__ == "__main__":
    main()
