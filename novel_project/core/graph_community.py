"""
人物社群检测与分层摘要
=====================
基于 nano-graphrag 的核心思想：在人物关系图上运行社区检测，
对每个社区生成摘要，作为检索的中间层（介于单个实体和全局之间）。

社区检测: NetworkX 内置 Louvain / greedy_modularity_communities
社区摘要: LLM 生成 200 字社群描述
持久化:  追加到 data/wiki/{novel}_graph.json 的 communities 字段
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from typing import Any

import networkx as nx

from core.llm import call_llm

logger = logging.getLogger(__name__)


# ── 社区检测 ──────────────────────────────────────────────────────────────

def detect_communities(G: nx.Graph) -> dict[str, int]:
    """
    在人物关系图上运行模块度社区检测。

    使用 NetworkX 内置 greedy_modularity_communities（Louvain 变体），
    不引入 python-igraph 额外依赖。

    返回: {节点名: 社区 ID}，社区 ID 按大小降序排列。
    """
    if G.number_of_nodes() < 3:
        # 图太小，不做社区检测
        return {n: 0 for n in G.nodes()}

    try:
        from networkx.algorithms.community import greedy_modularity_communities
        raw_communities = list(greedy_modularity_communities(G))
    except Exception:
        logger.warning("[Community] 社区检测失败，使用连通分量")
        raw_communities = list(nx.connected_components(G))

    # 按社区大小降序排列，最大的社区 ID=0
    raw_communities.sort(key=len, reverse=True)

    result: dict[str, int] = {}
    for cid, nodes in enumerate(raw_communities):
        for node in nodes:
            result[node] = cid

    logger.info(
        "[Community] 社区检测完成: %d 节点 → %d 社区 (前3: %s)",
        G.number_of_nodes(), len(raw_communities),
        [(cid, len(nodes)) for cid, nodes in enumerate(raw_communities[:3])],
    )
    return result


# ── 社区摘要 ──────────────────────────────────────────────────────────────

COMMUNITY_SUMMARY_PROMPT = """你是一个网文分析专家。以下是一组在人物关系图谱中被归入同一社群的角色。

小说：{novel}
社群标签（自动生成）：{label}

社群成员：
{members}

成员间的关系（部分）：
{relations}

请生成一段 200 字以内的社群描述，说明：
1. 这群角色在故事中的共同定位（阵营、派系、身份群体）
2. 他们之间的核心互动关系
3. 这群人在剧情中的主要作用

只返回描述文本，不要加标题或其他格式。"""


def _member_key(members: list[str]) -> str:
    """社区成员集合的稳定哈希（增量编译时判定缓存命中用）"""
    import hashlib
    return hashlib.sha1("|".join(sorted(members)).encode("utf-8")).hexdigest()


def generate_community_summaries(
    G: nx.Graph,
    communities: dict[str, int],
    wiki_entries: list[dict] | None = None,
    novel: str = "",
    cached_summaries: list[dict] | None = None,
) -> list[dict]:
    """
    为每个社区生成摘要（LLM 调用 + 规则兜底 + 成员集缓存）。

    参数:
        G: 人物关系图
        communities: detect_communities 的输出
        wiki_entries: Wiki 条目（用于获取角色描述，可选）
        novel: 小说名
        cached_summaries: 上次编译的社区摘要（可选）。成员集合未变的社区
                          直接复用旧摘要，跳过 LLM 调用（增量编译用）。

    返回:
        [{community_id, label, characters: [str], member_count, summary, top_relation, member_key}]
    """
    # 建立 成员集哈希 → 旧摘要 映射（兼容无 member_key 的旧格式：
    # characters 未截断时可以重建 key）
    cache_map: dict[str, str] = {}
    for s in cached_summaries or []:
        key = s.get("member_key", "")
        if not key:
            chars = s.get("characters", [])
            if chars and s.get("member_count", 0) == len(chars):
                key = _member_key(chars)
        if key and s.get("summary"):
            cache_map[key] = s["summary"]

    # 按社区分组
    groups: dict[int, list[str]] = {}
    for node_name, cid in communities.items():
        groups.setdefault(cid, []).append(node_name)

    summaries = []
    cache_hits = 0

    for cid in sorted(groups.keys()):
        members = groups[cid]
        if len(members) < 2:
            continue  # 单人社区不需要摘要

        member_key = _member_key(members)

        # 提取社区内部关系
        internal_edges = []
        for u, v, data in G.edges(members, data=True):
            if u in members and v in members:
                internal_edges.append(f"  {u} --[{data.get('relation', '')}]--> {v}")

        # 社区标签（占比最高的角色类型）
        label = _community_label(G, members)

        # 角色列表（含简要描述）
        member_descs = []
        for m in members[:15]:
            role = G.nodes[m].get("role", "")
            mention = G.nodes[m].get("mention_count", 0)
            member_descs.append(f"  {m} (角色: {role}, 出场: {mention}次)")

        is_large_community = len(members) >= 10

        summary = ""
        if member_key in cache_map:
            # 成员集合未变 → 复用旧摘要，跳过 LLM
            summary = cache_map[member_key]
            cache_hits += 1
        elif is_large_community:
            # LLM 生成社区摘要
            prompt = COMMUNITY_SUMMARY_PROMPT.format(
                novel=novel or "未知小说",
                label=label,
                members="\n".join(member_descs[:20]),
                relations="\n".join(internal_edges[:15]),
            )
            try:
                response = call_llm([{"role": "user", "content": prompt}])
                if response:
                    summary = response.strip()[:300]
            except Exception:
                pass

        if not summary:
            # 规则兜底
            roles = Counter(G.nodes[m].get("role", "") for m in members)
            top_role = roles.most_common(1)[0][0] if roles else "未知"
            summary = f"{label}（{len(members)}人，主要为{top_role}）"

        top_relation = _top_relation_type(G, members)

        summaries.append({
            "community_id": cid,
            "label": label,
            "characters": members[:20],
            "member_count": len(members),
            "summary": summary,
            "top_relation": top_relation,
            "member_key": member_key,
        })

    logger.info("[Community] 生成 %d 个社区摘要 (%d 个已 LLM 生成, %d 个复用缓存)",
                len(summaries), sum(1 for s in summaries if len(s["summary"]) > 50) - cache_hits,
                cache_hits)
    return summaries


def _community_label(G: nx.Graph, members: list[str]) -> str:
    """根据社区内角色类型占比生成标签"""
    roles = [G.nodes[m].get("role", "") for m in members if G.nodes[m].get("role")]
    if not roles:
        return "未知群体"
    top_role = Counter(roles).most_common(1)[0][0]
    if top_role == "主角":
        return "主角核心圈"
    elif top_role == "反派":
        return "反派阵营"
    elif top_role == "配角":
        return "重要配角群"
    else:
        return f"{top_role}群体"


def _top_relation_type(G: nx.Graph, members: list[str]) -> str:
    """社区内最常见的关系类型"""
    rel_types = []
    for u, v, data in G.edges(members, data=True):
        rel = data.get("relation", "")
        if rel:
            rel_types.append(rel)
    if not rel_types:
        return "关联"
    return Counter(rel_types).most_common(1)[0][0]


# ── 持久化 ────────────────────────────────────────────────────────────────

def save_community_data(
    communities: dict[str, int],
    summaries: list[dict],
    graph_filepath: str,
) -> None:
    """
    将社区检测结果和社区摘要追加到图谱 JSON 文件中。

    不覆盖原始数据，只追加 communities 字段。
    """
    if not os.path.exists(graph_filepath):
        logger.warning("[Community] 图谱文件不存在: %s", graph_filepath)
        return

    # 加载现有图谱
    with open(graph_filepath, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    # 追加社区数据
    node_to_community = {name: cid for name, cid in communities.items()}
    graph_data["communities"] = {
        "node_to_community": node_to_community,
        "summaries": summaries,
        "total_communities": len(set(communities.values())),
    }

    # 原子写入
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=os.path.dirname(graph_filepath))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, graph_filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    logger.info("[Community] 社区数据已写入: %s (%d 社区)", graph_filepath, len(summaries))


def load_community_data(graph_filepath: str) -> dict | None:
    """从图谱 JSON 加载社区数据"""
    if not os.path.exists(graph_filepath):
        return None
    with open(graph_filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("communities")
