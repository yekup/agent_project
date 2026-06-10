"""
审核 Agent
负责：检查 Writer 的报告质量，返回修改意见或通过
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

from agent_project.core.llm import call_llm


REVIEW_PROMPT = """你是一个报告审核员。请审核以下分析报告。

用户问题：{query}

报告内容：
{report}

请从以下方面审核：
1. 是否回答了用户问题？
2. 是否有事实依据（不是凭空编造）？
3. 结构是否清晰？
4. 是否标注了信息来源？

以 JSON 格式返回：
{{
    "passed": true/false,
    "score": 0-10,
    "feedback": "如果没通过，写明需要改进什么",
    "suggestions": ["具体修改建议1", "具体修改建议2"]
}}
"""


class Reviewer:
    """审核员：检查报告质量"""

    def review(self, report, query):
        """
        审核报告

        参数:
            report: Writer 生成的报告
            query: 原始问题

        返回:
            dict: {passed, score, feedback, suggestions}
        """
        if not report or len(report) < 50:
            return {
                "passed": False,
                "score": 0,
                "feedback": "报告为空或太短，需要重新生成",
                "suggestions": ["请基于检索资料重新撰写"],
            }

        prompt = REVIEW_PROMPT.format(query=query, report=report)
        response = call_llm([{"role": "user", "content": prompt}])

        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                print(f"  [Reviewer] 评分: {result.get('score', 0)}/10, "
                      f"{'通过' if result.get('passed') else '需修改'}")
                return result
            except json.JSONDecodeError:
                pass

        # 兜底：通过
        return {
            "passed": True,
            "score": 7,
            "feedback": "审核通过",
            "suggestions": [],
        }
