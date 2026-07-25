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
            from core.chunker import VectorStoreIndexer
            self._vector_indexer = VectorStoreIndexer()
        except Exception:
            pass

        # 原文索引(用于根据章节取原文)
        with open(novel_path,"r",encoding="utf-8") as f:
            novel_data = json.load(f)
        self.chapters = novel_data["chapters"]

        logger.info(f"三级检索器初始化完成：")
        logger.info(f"  Wiki: {len(self.wiki)} 章" + (f" + {len(self.volumes)} 卷 + 全书摘要" if self._has_hierarchy else ""))
        logger.info(f"  图谱: {self.graph.number_of_nodes()} 人物, {self.graph.number_of_edges()} 关系")
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
    
    def search_by_vector(self, query, top_k=20):
        """
        第 3 级：向量检索原文。
        通过 ChromaDB 对分块后的原文进行语义搜索。
        """
        if self._vector_indexer is None:
            return [{"text": "向量库未就绪", "metadata": {}, "score": 0}]

        try:
            results = self._vector_indexer.search(query, top_k=top_k)
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

    def search(self, query, top_k=3):
        """
        检索：Wiki → 图谱 → 向量 → 对话 Wiki
        返回汇总结果
        """
        result = {
            "query": query,
            "wiki_results": [],
            "graph_results": {"matched_nodes": [], "relations": []},
            "vector_results": [],
            "dialogue_results": [],
            "summary": "",
        }

        # 第 1 级：Wiki 检索
        wiki_results = self.search_wiki(query, top_k=top_k)
        result["wiki_results"] = wiki_results

        # 第 2 级：图谱检索
        graph_results = self.search_by_graph(query)
        result["graph_results"] = graph_results

        # 第 3 级：向量检索
        vector_results = self.search_by_vector(query, top_k=top_k)
        result["vector_results"] = vector_results

        # 第 4 级：对话 Wiki 检索
        dialogue_results = self.search_dialogue_wiki(query, top_k=top_k)
        result["dialogue_results"] = dialogue_results

        # 组装摘要
        summary_parts = []
        if wiki_results:
            chapters = [w.get("chapter_title") or w.get("title", "") for w in wiki_results]
            summary_parts.append(f"相关章节：{'、'.join(chapters)}")
        if graph_results["matched_nodes"]:
            nodes = graph_results["matched_nodes"]
            summary_parts.append(f"相关人物：{'、'.join(nodes)}")
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



        