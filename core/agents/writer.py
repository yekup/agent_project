"""
撰稿 Agent
负责：根据 Researcher 收集的资料，撰写分析报告
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

from agent_project.core.llm import call_llm


WRITER_PROMPT = """你是一个网文分析专家。请根据 Researcher 提供的资料，写一份分析报告。

用户问题：{query}
分析类型：{intent}

检索到的资料：
{materials}

要求：
1. 报告结构清晰，使用标题和小标题
2. 引用要标注来源（如「引自第一章 空谈」）
3. 如果资料不足，如实说明
4. 语言简洁，避免空话套话
5. 字数控制在 500-800 字
"""


class Writer:
    """撰稿人：基于检索资料撰写分析报告"""

    def write(self, query, intent, materials):
        """
        生成分析报告

        参数:
            query: 原始问题
            intent: 意图类型
            materials: Researcher 收集的资料列表
                [{step, description, result}, ...]

        返回:
            str: 分析报告文本
        """
        # 格式化资料
        formatted = []
        for m in materials:
            if m["result"]:
                formatted.append(f"【步骤 {m['step']}】{m['description']}\n{m['result']}")

        all_materials = "\n\n".join(formatted) if formatted else "（无检索结果）"

        prompt = WRITER_PROMPT.format(
            query=query,
            intent=intent,
            materials=all_materials,
        )

        report = call_llm([{"role": "user", "content": prompt}])
        print(f"  [Writer] 报告生成完成 ({len(report)} 字)")
        return report
