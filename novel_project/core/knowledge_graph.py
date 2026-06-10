"""
知识图谱构建
把Wiki条目中的人物实体跨章节合并，构建人物关系网络
输出：NetworkX图+JSON序列化
"""

import json
import os
import re

#需要安装networkx
#pip install networkx

import networkx as nx

def merge_characters(wiki_entries):
    """
    跨章合并人物实体
    
    同一人物在不同章节可能有不同称呼（萧炎、炎帝、萧炎小子）
    这里通过核心名称做简单合并
    
    参数:
        wiki_entries: build_wiki 的输出，每章一条 Wiki 条目
    
    返回:
        dict: {人物名: {出场章节列表、角色描述、总提及次数}}
    """
    char_map = {}  # 人物名 -> 信息聚合

    for entry in wiki_entries:
        chapter_title = entry.get("chapter_title", "")
        for c in entry.get("characters", []):
            name = c["name"]
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

def merge_relationships(wiki_entries):
    """
    跨章合并人物关系
    
    参数:
        wiki_entries: build_wiki 的输出
    
    返回:
        list of dict: [{source, target, relation, chapters, weight}]
    """
    rel_map = {}  # (source, target) -> 信息聚合

    for entry in wiki_entries:
        chapter_title = entry.get("chapter_title", "")
        for r in entry.get("relationships", []):
            source = r["source"]
            target = r["target"]
            key = (source, target)

            if key not in rel_map:
                rel_map[key] = {
                    "source": source,
                    "target": target,
                    "relation": r["relation"],
                    "chapters": [],
                    "weight": 0,
                }
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
        networkx.Graph 对象
    """
    G = nx.Graph()

    # 添加节点（人物）
    for name, info in char_map.items():
        G.add_node(
            name,
            role=info["role"],
            mention_count=info["mention_count"],
            chapter_count=len(info["chapters"]),
        )

    # 添加边（关系）
    for r in relationships:
        if r["source"] in char_map and r["target"] in char_map:
            G.add_edge(
                r["source"],
                r["target"],
                relation=r["relation"],
                weight=r["weight"],
                chapters=r["chapters"],
            )

    print(f"图构建完成：{G.number_of_nodes()} 个节点, {G.number_of_edges()} 条边")
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
    print(f"图已保存: {filepath} ({len(data['nodes'])} 节点, {len(data['edges'])} 条边)")
    return data


def load_graph(filepath):
    """从 JSON 加载图"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    G = nx.Graph()
    for n in data["nodes"]:
        name = n.pop("name")
        G.add_node(name, **n)
    for e in data["edges"]:
        source = e.pop("source")
        target = e.pop("target")
        G.add_edge(source, target, **e)
    print(f"图已加载: {G.number_of_nodes()} 节点, {G.number_of_edges()} 条边")
    return G

    