"""
研究员 Agent
负责：根据 Coordinator 分配的任务，在 Wiki/图谱/原文中检索信息
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))


class Researcher:
    """研究员：执行检索任务，返回结构化的资料"""

    def __init__(self, retriever):
        """
        参数:
            retriever: NovelRetriever 实例（三级检索器）
        """
        self.retriever = retriever

    def execute(self, description, query, intent):
        """
        执行一条检索任务
        
        参数:
            description: 任务描述（Coordinator 分配）
            query: 用户原始问题
            intent: 意图类型
        
        返回:
            str: 检索到的资料文本
        """
        # 根据任务描述中的关键词，选择检索方式
        desc_lower = description.lower()

        # 判断检索目标
        if any(kw in desc_lower for kw in ["wiki", "章节", "摘要"]):
            return self._search_wiki(query, description)
        elif any(kw in desc_lower for kw in ["图谱", "关系", "人物"]):
            return self._search_graph(query, description)
        elif any(kw in desc_lower for kw in ["原文", "细节", "段落"]):
            return self._search_original(query, description)
        else:
            # 兜底：全量搜索
            return self._search_all(query)

    def _search_wiki(self, query, description):
        """检索 Wiki"""
        results = self.retriever.search_wiki(query, top_k=5)
        if not results:
            # 兜底：直接取原文前几章或匹配关键词的章节
            import re
            match = re.search(r'前(\d+)章', query)
            n = int(match.group(1)) if match else 5
            lines = ["【原文检索（Wiki 无结果，走原文兜底）】"]
            for ch in self.retriever.chapters[:n]:
                lines.append(f"章节：{ch['title']}")
                text = ch["text"][:500]
                lines.append(text)
                lines.append("")
            return "\n".join(lines) if len(lines) > 1 else "Wiki 和原文中都未找到相关信息。"

        lines = ["【Wiki 检索结果】"]
        for w in results:
            title = w.get("chapter_title") or w.get("title", "")
            lines.append(f"章节：{title}")
            lines.append(f"摘要：{w.get('summary', '')}")
            chars = "、".join([c["name"] for c in w.get("characters", [])[:5]]) or "、".join(w.get("main_characters", [])[:5])
            if chars:
                lines.append(f"人物：{chars}")
            lines.append("")
        return "\n".join(lines)

    def _search_graph(self, query, description):
        """检索知识图谱"""
        result = self.retriever.search_by_graph(query)
        if not result["matched_nodes"] and not result["relations"]:
            return "知识图谱中未找到相关信息。"

        lines = ["【知识图谱检索结果】"]
        if result["matched_nodes"]:
            lines.append(f"匹配人物：{'、'.join(result['matched_nodes'])}")
        for r in result["relations"][:10]:
            lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")
        return "\n".join(lines)

    def _search_original(self, query, description):
        """检索原文：先按章节名直接找，再按关键词匹配"""
        import re

        desc_lower = (query + " " + description).lower()
        lines = ["【原文检索结果】"]

        # 策略 1：提取章节号（如"第四章""第4章"），直接取对应章节
        ch_match = re.search(r'第[一二三四五六七八九十\d]+[章回]', desc_lower)
        if ch_match:
            ch_text = ch_match.group()
            # 中文数字转数字
            cn_num = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
            ch_num = None
            for c, n in cn_num.items():
                if c in ch_text:
                    ch_num = n
                    break
            if ch_num is None:
                num_match = re.search(r'\d+', ch_text)
                if num_match:
                    ch_num = int(num_match.group())
            if ch_num and 1 <= ch_num <= len(self.retriever.chapters):
                ch = self.retriever.chapters[ch_num]
                lines.append(f"章节：{ch['title']}")
                lines.append(ch["text"][:1000])
                return "\n".join(lines)

        # 策略 2：直接遍历原文标题，匹配关键词
        for ch in self.retriever.chapters:
            title_lower = ch["title"].lower()
            # 对 description 中的每个有意义的词做匹配
            for word in desc_lower.split():
                if len(word) > 1 and word in title_lower:
                    lines.append(f"章节：{ch['title']}")
                    lines.append(ch["text"][:500])
                    lines.append("")
                    break
            if len(lines) > 10:  # 最多返回 5 章
                break

        if len(lines) > 1:
            return "\n".join(lines)

        # 策略 3：兜底——直接搜索 Wiki 找章节标题
        wiki_results = self.retriever.search_by_wiki(query, top_k=5)
        if wiki_results:
            for w in wiki_results:
                chapter_title = w["chapter_title"]
                for ch in self.retriever.chapters:
                    if ch["title"] == chapter_title:
                        lines.append(f"章节：{chapter_title}")
                        lines.append(ch["text"][:300])
                        lines.append("")
                        break
            if len(lines) > 1:
                return "\n".join(lines)

        return "未找到相关原文。"

    def _search_all(self, query):
        """全量搜索"""
        combined = self.retriever.search(query)
        parts = []

        if combined["summary"]:
            parts.append(f"概览：{combined['summary']}")

        if combined.get("vector_results"):
            for v in combined["vector_results"][:2]:
                parts.append(v.get("text", "")[:300])

        return "\n".join(parts)
