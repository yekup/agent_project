"""
知识图谱构建
把Wiki条目中的人物实体跨章节合并，构建人物关系网络
输出：NetworkX图+JSON序列化
"""

import json
import logging
import os
import re

#需要安装networkx
#pip install networkx

import networkx as nx

logger = logging.getLogger(__name__)

def _build_alias_map(wiki_entries):
    """
    基于共现关系和角色描述构建别名映射。

    规则:
        1. 如果人物名 A 包含在人物名 B 中（如"萧炎"和"萧炎小子"），自动合并
        2. 如果两个人物名在同一章节出现且 role 相同，可能是同一人
        3. 姓氏相同 + 高频(主角) + 低频(配角/路人) → 低频合并到高频
        4. 对高频人名做简单模糊匹配（去掉括号/备注后匹配）

    返回:
        dict: {规范名: [别名列表]}
    """
    all_names = set()
    name_info = {}

    for entry in wiki_entries:
        ch_title = entry.get("chapter_title", "")
        for c in entry.get("characters", []):
            name = c["name"]
            all_names.add(name)
            if name not in name_info:
                name_info[name] = {"chapters": set(), "roles": set()}
            name_info[name]["chapters"].add(ch_title)
            name_info[name]["roles"].add(c.get("role", ""))

    alias_map = {}
    names_list = sorted(all_names, key=lambda n: -len(name_info[n]["chapters"]))

    for i, name in enumerate(names_list):
        if name in alias_map:
            continue
        canonical = name
        alias_map[canonical] = []
        clean_canonical = name.split("（")[0].split("(")[0]

        for other in names_list[i + 1:]:
            if other in alias_map:
                continue
            clean_other = other.split("（")[0].split("(")[0]

            # 规则1: 包含关系（一个名字是另一个的子串，且不是单一姓氏）
            # "赵玖" → "赵玖（官家）"、"萧炎" → "萧炎小子"
            if clean_canonical and clean_other and len(clean_canonical) >= 2 and len(clean_other) >= 2:
                if clean_canonical in clean_other or clean_other in clean_canonical:
                    alias_map[canonical].append(other)
                    alias_map[other] = canonical
                    continue

            # 规则2（保守）: 姓氏相同 + 只出场1章 + 高频角色合并
            # "赵玖"和"赵管家"：共享"赵"，管家只出现1章
            if clean_canonical and clean_other and len(clean_canonical) >= 2 and len(clean_other) >= 2:
                if (clean_canonical[0] == clean_other[0] and clean_canonical != clean_other
                        and clean_canonical not in clean_other and clean_other not in clean_canonical):
                    other_count = len(name_info[other]["chapters"])
                    canonical_count = len(name_info[name]["chapters"])
                    # 只出场 1 章 + 高频 > 50 章 + 低频不是主角
                    if (other_count <= 1 and canonical_count > 50
                            and "主角" not in name_info[other]["roles"]):
                        alias_map[canonical].append(other)
                        alias_map[other] = canonical
                        continue

    return alias_map


def resolve_aliases(name: str, alias_map: dict) -> str:
    """将别名解析为规范名"""
    if name in alias_map:
        if isinstance(alias_map[name], list):
            return name
        else:
            return alias_map[name]
    return name


def merge_characters(wiki_entries, alias_map=None):
    """
    跨章合并人物实体

    同一人物在不同章节可能有不同称呼（萧炎、炎帝、萧炎小子）

    参数:
        wiki_entries: build_wiki 的输出，每章一条 Wiki 条目
        alias_map: _build_alias_map 的输出，None 则自动构建

    返回:
        dict: {人物名: {出场章节列表、角色描述、总提及次数}}
    """
    if alias_map is None:
        alias_map = _build_alias_map(wiki_entries)

    char_map = {}  # 规范名 -> 信息聚合

    for entry in wiki_entries:
        chapter_title = entry.get("chapter_title", "")
        for c in entry.get("characters", []):
            name = resolve_aliases(c["name"], alias_map)
            if name not in char_map:
                char_map[name] = {
                    "name": name,
                    "role": c["role"],
                    "descriptions": [],
                    "chapters": [],
                    "mention_count": 0,
                }
            char_map[name]["descriptions"].append(c["description"])
            char_map[name]["chapters"].append(chapter_title)
            char_map[name]["mention_count"] += 1

    return char_map

def merge_relationships(wiki_entries, alias_map=None):
    """
    跨章合并人物关系

    参数:
        wiki_entries: build_wiki 的输出
        alias_map: _build_alias_map 的输出

    返回:
        list of dict: [{source, target, relation, chapters, weight}]
    """
    if alias_map is None:
        alias_map = _build_alias_map(wiki_entries)

    rel_map = {}  # (source, target) -> 信息聚合

    for entry in wiki_entries:
        chapter_title = entry.get("chapter_title", "")
        for r in entry.get("relationships", []):
            source = resolve_aliases(r["source"], alias_map)
            target = resolve_aliases(r["target"], alias_map)
            key = (source, target)

            if key not in rel_map:
                rel_map[key] = {
                    "source": source,
                    "target": target,
                    "relation": r["relation"],
                    "chapters": [],
                    "weight": 0,
                }
            else:
                # 关系随剧情演化（敌→友等）：描述以最新章节的提取为准，
                # 权重仍累计全部出场次数
                rel_map[key]["relation"] = r["relation"]
            rel_map[key]["chapters"].append(chapter_title)
            rel_map[key]["weight"] += 1

    return list(rel_map.values())


def build_graph(char_map, relationships):
    """
    构建 NetworkX 人物关系图
    
    参数:
        char_map: merge_characters 的输出
        relationships: merge_relationships 的输出
    
    返回:
        networkx.DiGraph 对象（有向图：'A 是 B 的父亲' 与反向语义不同，
        历史版本用无向 Graph 会丢失关系方向）
    """
    G = nx.DiGraph()

    # 添加节点（人物）
    for name, info in char_map.items():
        G.add_node(
            name,
            role=info["role"],
            mention_count=info["mention_count"],
            chapter_count=len(info["chapters"]),
        )

    # 添加边（关系）
    dropped = 0
    self_loops = 0
    for r in relationships:
        # 自环边（上游实体合并把不同人物并到同名节点）无检索价值，丢弃
        if r["source"] == r["target"]:
            self_loops += 1
            continue
        if r["source"] in char_map and r["target"] in char_map:
            G.add_edge(
                r["source"],
                r["target"],
                relation=r["relation"],
                weight=r["weight"],
                chapters=r["chapters"],
            )
        else:
            dropped += 1
    if dropped:
        logger.warning(f"  ⚠️ {dropped} 条关系因端点人物缺失被丢弃")
    if self_loops:
        logger.warning(f"  ⚠️ {self_loops} 条自环关系被丢弃")

    logger.info(f"图构建完成：{G.number_of_nodes()} 个节点, {G.number_of_edges()} 条边")
    return G


def save_graph(G, filepath):
    """保存图到 JSON（NetworkX 的节点/边转可序列化格式）"""
    data = {
        "nodes": [
            {
                "name": n,
                **G.nodes[n]
            }
            for n in G.nodes
        ],
        "edges": [
            {
                "source": u,
                "target": v,
                **G.edges[u, v]
            }
            for u, v in G.edges
        ]
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"图已保存: {filepath} ({len(data['nodes'])} 节点, {len(data['edges'])} 条边)")
    return data


def load_graph(filepath):
    """从 JSON 加载图（DiGraph，与 build_graph 一致保留方向；自环边在加载时过滤）"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for n in data["nodes"]:
        name = n.pop("name")
        G.add_node(name, **n)
    self_loops = 0
    for e in data["edges"]:
        source = e.pop("source")
        target = e.pop("target")
        if source == target:
            self_loops += 1
            continue
        G.add_edge(source, target, **e)
    if self_loops:
        logger.info(f"已过滤 {self_loops} 条自环边")
    logger.info(f"图已加载: {G.number_of_nodes()} 节点, {G.number_of_edges()} 条边")
    return G

    