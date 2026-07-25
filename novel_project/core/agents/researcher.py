"""
研究员 Agent
负责：根据 Coordinator 分配的任务，在 Wiki/图谱/原文中检索信息
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any


class Researcher:
    """研究员：执行检索任务，返回结构化的资料"""

    def __init__(self, retriever):
        self.retriever = retriever

    def execute(self, description, query, intent):
        desc_lower = description.lower()

        # 对话 Wiki 优先路由: 描述含「讨论/之前/上次」等关键词
        if any(kw in desc_lower for kw in ["讨论", "之前", "上次", "历史", "对话"]):
            return self._search_dialogue(query)
        elif any(kw in desc_lower for kw in ["向量", "语义", "全文"]):
            return self._search_vector(query)
        elif any(kw in desc_lower for kw in ["wiki", "章节", "摘要"]):
            return self._search_wiki(query, description)
        elif any(kw in desc_lower for kw in ["图谱", "关系", "人物"]):
            return self._search_graph(query, description)
        elif any(kw in desc_lower for kw in ["原文", "细节", "段落"]):
            return self._search_original(query, description)
        else:
            return self._search_all(query)

    # ── 工具 ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_entities(query: str) -> list[str]:
        """从问题中提取可能的人名/实体名"""
        # 常见的连接词
        delimiters = ["和", "与", "、", "，", " ", "以及", "跟", "的关系", "在", "的"]
        entities = [query]
        for d in delimiters:
            if d in query:
                parts = [p.strip() for p in query.split(d) if len(p.strip()) >= 2]
                entities.extend(parts)
        # 去重
        seen = set()
        result = []
        for e in entities:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result[:5]

    @staticmethod
    def _dedup_results(results: list[dict], key: str = "chapter_title") -> list[dict]:
        """按章节标题去重，保留第一条"""
        seen = set()
        deduped = []
        for r in results:
            k = r.get(key, "")
            if k and k not in seen:
                seen.add(k)
                deduped.append(r)
        return deduped

    # ── 向量检索（增强版） ─────────────────────────────────────────

    def _search_vector(self, query: str) -> str:
        """
        多查询扩展向量检索：
        1. 原问题检索（top_k=15）
        2. 拆分实体对检索（top_k=10）
        3. 合并去重后输出
        """
        seen_chapters = set()
        all_results: list[dict] = []

        # 1. 原问题检索
        results = self._safe_vector_search(query, top_k=15)
        for r in results:
            ch = r.get("metadata", {}).get("chapter_title", "")
            if ch and ch not in seen_chapters:
                seen_chapters.add(ch)
                all_results.append(r)

        # 2. 实体对扩展检索
        entities = self._extract_entities(query)
        if len(entities) > 1:
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    if entities[i] != entities[j] and len(entities[i]) >= 2 and len(entities[j]) >= 2:
                        pair_query = f"{entities[i]} {entities[j]}"
                        pair_results = self._safe_vector_search(pair_query, top_k=8)
                        for r in pair_results:
                            ch = r.get("metadata", {}).get("chapter_title", "")
                            if ch and ch not in seen_chapters:
                                seen_chapters.add(ch)
                                all_results.append(r)

        if not all_results:
            return "【向量检索】未找到语义匹配的段落。"

        lines = ["【原文段落检索（向量语义匹配）】"]
        for r in all_results[:25]:
            text = r.get("text", "")[:400]
            meta = r.get("metadata", {})
            ch_title = meta.get("chapter_title", "") or ""
            score = r.get("score", "")
            score_str = f" (r={1-score:.2f})" if isinstance(score, (int, float)) and score != 0 else ""
            lines.append(f"  📄 [{ch_title}]{score_str}")
            lines.append(f"    {text}")
            lines.append("")
        return "\n".join(lines)

    def _safe_vector_search(self, query: str, top_k: int = 10) -> list[dict]:
        try:
            return self.retriever.search_by_vector(query, top_k=top_k)
        except Exception:
            return []

    # ── Wiki 检索（增强版） ─────────────────────────────────────────

    def _search_wiki(self, query: str, description: str) -> str:
        """Wiki 检索 + 自动附加向量检索"""
        results = self.retriever.search_wiki(query, top_k=40)

        lines = ["【Wiki 检索结果】"]

        # 全书摘要
        book = getattr(self.retriever, "book", None)
        if book and book.get("summary"):
            lines.append("【全书概要】")
            lines.append(book["summary"][:500])
            lines.append("")

        # 卷摘要
        from core.text_match import ngram_hits
        volume_lines = []
        for vol in getattr(self.retriever, "volumes", []):
            vs = vol.get("summary", "")
            vt = vol.get("title", "")
            if vs and ngram_hits(query, vs) >= 2:
                volume_lines.append(f"  {vt}: {vs[:200]}")
        if volume_lines:
            lines.append("【相关卷摘要】")
            lines.extend(volume_lines[:5])
            lines.append("")

        # 章节摘要（去重）
        seen_titles = set()
        chapter_lines = []
        for w in results:
            title = w.get("chapter_title") or w.get("title", "")
            if title in seen_titles:
                continue
            seen_titles.add(title)
            chapter_lines.append(f"章节：{title}")
            chapter_lines.append(f"摘要：{w.get('summary', '')}")
            chars = "、".join([c.get("name", "") for c in w.get("characters", [])[:5] if c.get("name")]) or "、".join(w.get("main_characters", [])[:5])
            if chars:
                chapter_lines.append(f"人物：{chars}")
            chapter_lines.append("")
            if len(chapter_lines) > 30:
                break

        if chapter_lines:
            lines.append("【相关章节】")
            lines.extend(chapter_lines)

        wiki_text = "\n".join(lines) if len(lines) > 1 else ""

        # 补充向量检索（原文全文）
        vector_text = self._search_vector(query)

        combined = wiki_text
        if vector_text and "未找到" not in vector_text[:20]:
            combined += "\n" + vector_text

        return combined if combined else "Wiki 中未找到相关信息。"

    # ── 图谱检索 ───────────────────────────────────────────────────

    def _search_graph(self, query: str, description: str) -> str:
        """检索知识图谱"""
        result = self.retriever.search_by_graph(query)
        if not result["matched_nodes"] and not result["relations"]:
            return "知识图谱中未找到相关信息。"

        lines = ["【知识图谱检索结果】"]
        if result["matched_nodes"]:
            lines.append(f"匹配人物：{'、'.join(result['matched_nodes'])}")
        for r in result["relations"][:15]:
            lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")
        return "\n".join(lines)

    # ── 原文检索 ───────────────────────────────────────────────────

    def _search_original(self, query: str, description: str) -> str:
        """检索原文（关键词 + 向量兜底）"""
        lines = ["【原文检索结果】"]

        # 策略 1：提取章节号直接取（按标题编号定位，兼容前言偏移）
        from core.cn_num import extract_chapter_number, find_chapter_by_number
        ch_num = extract_chapter_number(query) or extract_chapter_number(description)
        if ch_num:
            ch = find_chapter_by_number(self.retriever.chapters, ch_num)
            if ch:
                lines.append(f"章节：{ch['title']}")
                lines.append(ch["text"][:1000])
                return "\n".join(lines)

        # 策略 2：关键词匹配标题（中文 n-gram，不再按空格分词）
        from core.text_match import ngram_hits
        for ch in self.retriever.chapters:
            if ngram_hits(query, ch["title"]) >= 2:
                lines.append(f"章节：{ch['title']}")
                lines.append(ch["text"][:500])
                lines.append("")
            if len(lines) > 10:
                break

        if len(lines) > 1:
            return "\n".join(lines)

        # 策略 3：向量兜底
        vector_text = self._search_vector(query)
        if vector_text and "未找到" not in vector_text[:20]:
            return vector_text

        return "未找到相关原文。"

    # ── 全量搜索 ───────────────────────────────────────────────────

    def _search_dialogue(self, query: str) -> str:
        """检索对话 Wiki（讨论结论层）"""
        try:
            results = self.retriever.search_dialogue_wiki(query, top_k=5)
        except Exception:
            return ""

        if not results:
            return ""

        lines = ["【已有讨论结论（对话 Wiki）】"]
        for r in results:
            lines.append(f"  主题：{r.get('topic', '')}")
            lines.append(f"  结论：{r.get('conclusion', '')[:200]}")
            points = r.get("key_points", [])
            if points:
                lines.append(f"  要点：{'；'.join(points[:3])}")
            entities = r.get("entities", [])
            if entities:
                lines.append(f"  相关实体：{'、'.join(entities[:5])}")
            lines.append("")
        return "\n".join(lines)

    def _search_all(self, query: str) -> str:
        """全量搜索：Wiki + 向量 + 图谱 + 对话 Wiki"""
        parts = []

        # Wiki
        wiki_text = self._search_wiki(query, "")

        # 图谱
        graph_result = self.retriever.search_by_graph(query)
        graph_text = ""
        if graph_result.get("matched_nodes"):
            graph_text = f"【知识图谱】\n匹配人物：{'、'.join(graph_result['matched_nodes'][:5])}"
            if graph_result["relations"]:
                graph_text += "\n关系："
                for r in graph_result["relations"][:10]:
                    graph_text += f"\n  {r['source']} --[{r['relation']}]--> {r['target']}"

        parts.append(wiki_text)
        if graph_text:
            parts.append(graph_text)

        # 对话 Wiki（标注来源，供 Writer 区分事实与讨论结论）
        dialogue_text = self._search_dialogue(query)
        if dialogue_text:
            parts.append(dialogue_text)

        return "\n\n".join(parts)
