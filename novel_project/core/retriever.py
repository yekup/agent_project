"""
三级检索器
先查Wiki->再查知识图谱->最后去原文取证
"""

import json
import logging
import os
import sys

from core.knowledge_graph import load_graph
from core.text_match import chapter_title_core, ngram_hits

logger = logging.getLogger(__name__)

class NovelRetriever:
    """网文三级检索器"""

    def __init__(self,wiki_path,graph_path,novel_path):
        """
        参数：
            wiki_path:wiki JSON文件路径（支持新旧格式）
            grapth_path:图谱JSON文件路径
            novel_path:清洗后的原小说JSON文件路径
        """
        # 第一级：wiki（支持三层结构）
        with open(wiki_path,"r",encoding="utf-8") as f:
            wiki_data = json.load(f)

        # 兼容新旧格式
        if isinstance(wiki_data, dict) and "chapters" in wiki_data:
            self.book = wiki_data.get("book", {})
            self.volumes = wiki_data.get("volumes", [])
            self.wiki = wiki_data["chapters"]
            self._has_hierarchy = bool(self.book.get("summary") or self.volumes)
        else:
            self.book = {"type": "book", "title": "全书总览", "summary": ""}
            self.volumes = []
            self.wiki = wiki_data if isinstance(wiki_data, list) else []
            self._has_hierarchy = False

        # 第二级：知识图谱
        self.graph = load_graph(graph_path)

        # 从 wiki_path 提取小说 key（用于对话 Wiki 文件定位）
        wiki_basename = os.path.basename(wiki_path)
        self._novel_key = wiki_basename.replace("_hierarchical.json", "").replace("_wiki.json", "")

        # 第三级：向量检索（初始化 ChromaDB 索引器）
        self._vector_indexer = None
        try:
            from core.chunker import VectorStoreIndexer, NOVEL_KEY_TO_SHORT
            self._vector_indexer = VectorStoreIndexer()
            # 主线程预热初始化（ONNX 模型加载），避免并行检索线程竞争首次导入
            self._vector_indexer._init_db()
            # 向量库 metadata 的 novel_key 用短名（shaosong/doupo/...），
            # 与 self._novel_key（wiki 全名）做一次映射
            self._vector_key = NOVEL_KEY_TO_SHORT.get(self._novel_key, self._novel_key)
        except Exception:
            self._vector_key = self._novel_key

        # 原文索引(用于根据章节取原文)
        with open(novel_path,"r",encoding="utf-8") as f:
            novel_data = json.load(f)
        self.chapters = novel_data["chapters"]

        # 社区数据（从图谱 JSON 加载，可能不存在）
        self._community_data: dict | None = None
        try:
            from core.graph_community import load_community_data
            self._community_data = load_community_data(graph_path) or {}
        except Exception:
            pass

        logger.info(f"三级检索器初始化完成：")
        logger.info(f"  Wiki: {len(self.wiki)} 章" + (f" + {len(self.volumes)} 卷 + 全书摘要" if self._has_hierarchy else ""))
        logger.info(f"  图谱: {self.graph.number_of_nodes()} 人物, {self.graph.number_of_edges()} 关系")
        if self._community_data:
            logger.info(f"  社区: {self._community_data.get('total_communities', 0)} 个")
        logger.info(f"  原文: {len(self.chapters)} 章")

    def _dynamic_top_k(self, query, default=5):
        """
        根据问题动态计算 top_k

        支持模式：
        - "前N章" → max(default, N)
        - "第X章到第Y章" → max(default, Y-X+1)
        - "全书/整本/所有章节" → 返回所有
        - 其他 → default
        """
        import re

        # 全局关键词
        if any(kw in query for kw in ["全书", "整本", "整本书", "所有章节", "全部内容", "全书总览"]):
            return len(self.wiki)  # 不限

        # "前N章"模式
        match = re.search(r'前(\d+)章', query)
        if match:
            return max(default, int(match.group(1)))

        # "第X章到第Y章"模式
        match = re.search(r'第(\d+)章到第(\d+)章', query)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            return max(default, end - start + 1)

        # "N章" 或 "第N章" 或 "前N章" 简短模式
        match = re.search(r'前(\d+)', query)
        if match:
            return max(default, int(match.group(1)))

        return default

    def _match_entry(self, entry, query_lower):
        """计算单条 Wiki 条目与查询的匹配分数（中文 n-gram 匹配）"""
        score = 0
        title = entry.get("chapter_title") or entry.get("title", "")
        summary = entry.get("summary", "")

        # 标题匹配：完整标题或核心名（去掉"第X章"）出现在问题中
        if title:
            core = chapter_title_core(title)
            if title.lower() in query_lower or (core and core.lower() in query_lower):
                score += 10

        # 摘要匹配：问题的 n-gram 命中摘要（≥2 防泛匹配）
        if summary and ngram_hits(query_lower, summary.lower()) >= 2:
            score += 5

        # 人物匹配（子串包含即可）
        for c in entry.get("characters", []):
            name = (c.get("name") or "").split("（")[0].split("(")[0]
            if name and name in query_lower:
                score += 10
                break
        for c in entry.get("main_characters", []):
            if c and c in query_lower:
                score += 10
                break

        # 事件匹配：问题的 n-gram 命中事件描述
        for e in entry.get("events", []):
            if e and ngram_hits(query_lower, e.lower()) >= 2:
                score += 5
                break

        return score

    def search_wiki(self, query, top_k=None):
        """
        第 1 级：检索 Wiki（支持三层级 + 动态 top_k）

        检索优先级：全书摘要 > 卷摘要 > 章节摘要

        参数:
            query: 用户问题
            top_k: 返回条数，None 则自动计算

        返回:
            list of dict
        """
        if top_k is None:
            top_k = self._dynamic_top_k(query)

        query_lower = query.lower()
        results = []

        # 检测是否问全局内容
        is_global_query = any(kw in query_lower for kw in ["全书", "整本", "整本书", "所有章节", "全部内容", "总结全书", "讲了什么"])

        # 第 1 优先：检查是否匹配全书摘要
        if self._has_hierarchy and self.book.get("summary"):
            score = self._match_entry(self.book, query_lower)
            if score > 0 or is_global_query:
                results.append(self.book)

        # 如果问全局但无全书摘要，直接返回所有章节
        if is_global_query and not self._has_hierarchy:
            return self.wiki[:top_k]

        # 第 2 优先：匹配卷摘要
        volume_results = []
        for vol in self.volumes:
            score = self._match_entry(vol, query_lower)
            if score > 0:
                volume_results.append((score, vol))
        volume_results.sort(key=lambda x: -x[0])
        results.extend([v for _, v in volume_results[:3]])

        # 如果问题涉及卷范围（如"前五章"在卷摘要里已经有了），提前返回
        if results and len(results) >= top_k:
            return results[:top_k]

        # 第 3 优先：匹配章节摘要
        chapter_scored = []
        for entry in self.wiki:
            score = self._match_entry(entry, query_lower)
            if score > 0:
                chapter_scored.append((score, entry))

        # 兜底
        if not chapter_scored:
            for entry in self.wiki:
                title = entry.get("chapter_title", "").lower()
                for word in query_lower.split():
                    if len(word) > 1 and word in title:
                        chapter_scored.append((3, entry))
                        break

        chapter_scored.sort(key=lambda x: -x[0])
        results.extend([e for _, e in chapter_scored[:top_k]])

        return results[:top_k]

    def search_by_graph(self, query):
        """
        第 2 级：检索知识图谱
        返回与 query 相关的人物节点和关系
        """
        query_lower = query.lower()
        related_nodes = set()
        relations = []

        for node in self.graph.nodes:
            # 去掉括号备注再匹配
            node_clean = node.split("（")[0].split("(")[0].lower()
            if node_clean and node_clean in query_lower:
                related_nodes.add(node)
            elif node.lower() in query_lower:
                related_nodes.add(node)

        # 找到这些节点的邻接节点和关系
        for node in related_nodes:
            for neighbor in self.graph.neighbors(node):
                edge_data = self.graph.get_edge_data(node, neighbor)
                relations.append({
                    "source": node,
                    "target": neighbor,
                    "relation": edge_data.get("relation", ""),
                    "weight": edge_data.get("weight", 1),
                })

        return {
            "matched_nodes": list(related_nodes),
            "relations": relations,
        }

    def search_by_ppr(self, query, top_k=10, alpha=0.85):
        """
        第 2.5 级：PPR 多跳图检索（HippoRAG 式）

        从查询中命中的实体节点出发，在人物关系图上做个性化 PageRank，
        把与查询实体"走得近"的人物按结构重要性排序——能发现查询中
        没有直接点名、但通过多跳关系紧密关联的人物（桥接人物）。

        参数:
            query: 用户问题
            top_k: 返回的人物数（含种子节点）
            alpha: 重启概率（越大越贴近种子，HippoRAG 常用 0.5~0.85）

        返回:
            {"seed_nodes": [...], "ppr_nodes": [{name, score, is_seed}], "relations": [...]}
            无种子命中时 ppr_nodes 为空。
        """
        import networkx as nx

        # 1. 种子节点：与 search_by_graph 相同的实体命中逻辑
        query_lower = query.lower()
        seeds = []
        for node in self.graph.nodes:
            node_clean = node.split("（")[0].split("(")[0].lower()
            if node_clean and node_clean in query_lower:
                seeds.append(node)
            elif node.lower() in query_lower:
                seeds.append(node)
        if not seeds:
            return {"seed_nodes": [], "ppr_nodes": [], "relations": []}

        # 2. 个性化 PageRank（边权重非法时退化为无权图）
        personalization = {n: 1.0 / len(seeds) for n in seeds}
        scores = None
        for kwargs in ({"weight": "weight"}, {}):
            try:
                scores = nx.pagerank(self.graph, alpha=alpha,
                                     personalization=personalization, **kwargs)
                break
            except Exception as e:
                logger.debug(f"PPR 计算失败（{kwargs}）: {e}")
        if scores is None:
            return {"seed_nodes": seeds, "ppr_nodes": [], "relations": []}

        # 3. 按 PPR 分数排序取 top_k
        seed_set = set(seeds)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        ppr_nodes = [
            {"name": name, "score": round(score, 6), "is_seed": name in seed_set}
            for name, score in ranked[:top_k]
        ]
        top_names = {p["name"] for p in ppr_nodes}

        # 4. top 节点之间的关系（含与种子的边，用于解释多跳路径）
        relations = []
        for u, v, data in self.graph.edges(data=True):
            if u in top_names and v in top_names:
                relations.append({
                    "source": u,
                    "target": v,
                    "relation": data.get("relation", ""),
                    "weight": data.get("weight", 1),
                })
        relations.sort(key=lambda r: -(r["weight"] if isinstance(r["weight"], (int, float)) else 1))

        return {
            "seed_nodes": seeds,
            "ppr_nodes": ppr_nodes,
            "relations": relations[:15],
        }

    def search_by_vector(self, query, top_k=20):
        """
        第 3 级：向量检索原文（混合检索）。

        两条腿：
        1. 纯向量语义检索（默认 ONNX 嵌入对中文较弱，单靠它召回率低）
        2. 实体精确腿：查询中命中图谱节点的实体名，用 where_document
           限定分块必须包含实体名，再按向量分排序——保证实体必现

        两腿结果按 RRF（Reciprocal Rank Fusion）融合排序。
        """
        if self._vector_indexer is None:
            return [{"text": "向量库未就绪", "metadata": {}, "score": 0}]

        try:
            # 第 1 腿：纯向量（限定本书，避免多书索引后跨书串扰）
            legs = [self._vector_indexer.search(query, top_k=top_k, novel_key=self._vector_key) or []]

            # 第 2 腿：实体精确（图谱节点名出现在查询中 → 限定包含）
            graph = getattr(self, "graph", None)
            if graph is not None:
                entities = []
                for node in graph.nodes:
                    clean = node.split("（")[0].split("(")[0]
                    if clean and len(clean) >= 2 and clean in query and clean not in entities:
                        entities.append(clean)
                for e in entities[:3]:
                    hits = self._vector_indexer.search(query, top_k=5, novel_key=self._vector_key, contains=e) or []
                    legs.append(hits)

            # RRF 融合（保留每条命中原有的 text/metadata/score 字段；
            # 实体腿权重 ×2：实体必现的 chunks 对实体类问题更可靠）
            fused: dict[str, float] = {}
            by_id: dict[str, dict] = {}
            for leg_idx, leg in enumerate(legs):
                leg_weight = 1.0 if leg_idx == 0 else 2.0
                for rank, hit in enumerate(leg):
                    cid = hit.get("chunk_id") or hit.get("text", "")[:50]
                    if not cid or "未就绪" in str(hit.get("text", "")):
                        continue
                    fused[cid] = fused.get(cid, 0.0) + leg_weight / (60 + rank + 1)
                    if cid not in by_id:
                        by_id[cid] = hit
            ordered = sorted(fused.items(), key=lambda kv: -kv[1])
            results = [by_id[cid] for cid, _ in ordered[:top_k]]
            return results if results else [{"text": "无匹配结果", "metadata": {}, "score": 0}]
        except Exception as e:
            return [{"text": f"向量检索异常: {e}", "metadata": {}, "score": 0}]

    def search_dialogue_wiki(self, query, top_k=3):
        """
        检索对话 Wiki（独立于章节 Wiki 的讨论结论文档层）。

        打分策略: 实体命中 ×5 + ngram_hits(query, topic + conclusion)

        返回带 source_type: "dialogue" 标记的结果列表。
        无匹配时返回空列表。
        """
        result = []
        dialogue_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "wiki", f"{self._novel_key}_dialogue.json",
        )
        if not os.path.exists(dialogue_path):
            return result

        try:
            with open(dialogue_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return result

        entries = data.get("entries", [])
        if not entries:
            return result

        scored = []
        for entry in entries:
            score = 0
            # 实体命中 ×5
            for entity in entry.get("entities", []):
                if entity and entity in query:
                    score += 5
            # n-gram 命中 topic + conclusion
            combined = (entry.get("topic", "") + " " + entry.get("conclusion", ""))
            try:
                score += ngram_hits(query, combined)
            except Exception:
                pass

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        for _, entry in scored[:top_k]:
            result.append({
                "source_type": "dialogue",
                "topic": entry.get("topic", ""),
                "conclusion": entry.get("conclusion", ""),
                "key_points": entry.get("key_points", []),
                "entities": entry.get("entities", []),
                "evidence_chapters": entry.get("evidence_chapters", []),
                "text": f"【讨论结论】{entry.get('topic', '')}：{entry.get('conclusion', '')}",
            })

        return result

    def search_communities(self, query, top_k=3):
        """
        社区级检索：在人物社群摘要中匹配关键词。

        打分: 实体命中 ×5 + ngram_hits(query, label + summary)
        社群数据不存在（未编译社区）时返回空列表。
        """
        if not self._community_data:
            return []

        summaries = self._community_data.get("summaries", [])
        node_map = self._community_data.get("node_to_community", {})
        if not summaries:
            return []

        scored = []
        for s in summaries:
            score = 0
            # 实体命中
            for name in s.get("characters", []):
                if name and name in query:
                    score += 5
            # n-gram 匹配 label + summary
            combined = (s.get("label", "") + " " + s.get("summary", ""))
            try:
                score += ngram_hits(query, combined)
            except Exception:
                pass

            if score > 0:
                scored.append((score, s))

        scored.sort(key=lambda x: -x[0])
        result = []
        for _, s in scored[:top_k]:
            chars = s.get("characters", [])
            result.append({
                "source_type": "community",
                "community_id": s.get("community_id", 0),
                "label": s.get("label", ""),
                "summary": s.get("summary", ""),
                "member_count": s.get("member_count", 0),
                "top_characters": chars[:8],
                "text": f"【人物社群】{s.get('label', '')}（{len(chars)}人）：{s.get('summary', '')}",
            })

        return result

    def search(self, query, top_k=3):
        """
        检索：Wiki → 图谱 → 向量 → 对话 Wiki
        返回汇总结果
        """
        result = {
            "query": query,
            "wiki_results": [],
            "graph_results": {"matched_nodes": [], "relations": []},
            "ppr_results": {"seed_nodes": [], "ppr_nodes": [], "relations": []},
            "vector_results": [],
            "community_results": [],
            "dialogue_results": [],
            "summary": "",
        }

        # 第 1 级：Wiki 检索
        wiki_results = self.search_wiki(query, top_k=top_k)
        result["wiki_results"] = wiki_results

        # 第 2 级：图谱检索
        graph_results = self.search_by_graph(query)
        result["graph_results"] = graph_results

        # 第 2.5 级：PPR 多跳检索（仅在图谱有种子命中时有产出）
        ppr_results = self.search_by_ppr(query, top_k=10)
        result["ppr_results"] = ppr_results

        # 第 3 级：向量检索
        vector_results = self.search_by_vector(query, top_k=top_k)
        result["vector_results"] = vector_results

        # 第 4 级：社群检索
        community_results = self.search_communities(query, top_k=top_k)
        result["community_results"] = community_results

        # 第 5 级：对话 Wiki 检索
        dialogue_results = self.search_dialogue_wiki(query, top_k=top_k)
        result["dialogue_results"] = dialogue_results

        # 组装摘要
        summary_parts = []
        if wiki_results:
            chapters = [w.get("chapter_title") or w.get("title", "") for w in wiki_results]
            summary_parts.append(f"相关章节：{'、'.join(chapters)}")
        if community_results:
            labels = [c.get("label", "") for c in community_results]
            summary_parts.append(f"相关社群：{'、'.join(labels)}")
        if graph_results["matched_nodes"]:
            nodes = graph_results["matched_nodes"]
            summary_parts.append(f"相关人物：{'、'.join(nodes)}")
        ppr_extra = [p["name"] for p in ppr_results["ppr_nodes"] if not p["is_seed"]]
        if ppr_extra:
            summary_parts.append(f"多跳关联人物：{'、'.join(ppr_extra[:5])}")
        if vector_results and vector_results[0].get("text") and "未就绪" not in vector_results[0]["text"]:
            summary_parts.append(f"向量匹配段落：{len(vector_results)} 条")
        if dialogue_results:
            summary_parts.append(f"已有讨论结论：{len(dialogue_results)} 条")
        result["summary"] = "；".join(summary_parts)

        return result


def format_search_result(result):
    """将三级检索结果格式化为可读文本"""
    lines = []
    lines.append(f"查询：{result['query']}")
    lines.append("")

    # Wiki 结果
    if result["wiki_results"]:
        lines.append("【相关章节】")
        for w in result["wiki_results"]:
            lines.append(f"  📖 {w['chapter_title']}")
            lines.append(f"     摘要：{w['summary'][:100]}")
            chars = "、".join([c["name"] for c in w["characters"][:5]])
            if chars:
                lines.append(f"     人物：{chars}")
        lines.append("")

    # 图谱结果
    if result["graph_results"]["matched_nodes"]:
        lines.append("【知识图谱】")
        nodes = result["graph_results"]["matched_nodes"]
        lines.append(f"  匹配人物：{'、'.join(nodes)}")
        for r in result["graph_results"]["relations"][:10]:
            lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")
        lines.append("")

    return "\n".join(lines)



        