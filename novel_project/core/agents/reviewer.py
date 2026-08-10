"""
审核 Agent
负责：检查 Writer 的报告质量，返回修改意见或通过
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

from core.llm import call_llm


REVIEW_PROMPT = """你是一个网文分析报告审核员。请严格审核以下分析报告。

用户问题：{query}

检索到的原文片段（用于验证引用真实性）：
{research_materials}

报告内容：
{report}

请从五方面审核：
1. 是否回答了用户问题？如偏离主题则判定不通过
2. 每条结论是否有原文依据？如出现原文未提及的人物/事件/关系，属于幻觉
3. 人物关系描述是否准确？如将两人关系写反或张冠李戴，属于别名/实体冲突
4. 报告中的引用（「引自第X章」）是否能在【可引用章节白名单】或检索到的原文片段中找到对应的章节名？只有引用了两者都不存在的章节，才属于编造引用
5. 结构是否清晰、来源是否标注？

以 JSON 格式返回：
{{
    "passed": true/false,
    "score": 0-10,
    "failure_type": "evidence_missing",（不通过时必须填写）
                 "alias_conflict",（人物关系写反或张冠李戴）
                 "hallucination",（编造原文不存在的内容）
                 "fake_citation",（编造不存在的章节引用）
                 "irrelevant",（未回答用户问题）
                 "other"（其他原因）
    "feedback": "具体说明问题所在",
    "suggestions": ["具体修改建议1", "具体修改建议2"],
    "missing_entities": ["用户问到但报告中未覆盖的人物/事件"],
    "retrieval_hints": ["需要补充检索的关键词或方向"]
}}
"""


class Reviewer:
    """审核员：检查报告质量"""

    def review(self, report, query, research_materials: str = ""):
        """
        审核报告，返回结构化结果。

        参数:
            report: Writer 生成的报告文本
            query: 用户原始问题
            research_materials: Researcher 检索到的原文片段（用于验证引用）

        返回:
            dict: {
                "passed": bool,
                "score": int,
                "failure_type": str,
                "feedback": str,
                "suggestions": list[str],
                "missing_entities": list[str],
                "retrieval_hints": list[str],
            }
        """
        if not report or len(report) < 50:
            return {
                "passed": False,
                "score": 0,
                "failure_type": "evidence_missing",
                "feedback": "报告为空或太短，需要重新生成",
                "suggestions": ["请基于检索资料重新撰写"],
                "missing_entities": [],
                "retrieval_hints": [],
            }

        # 截取研究材料前 8000 字供 reviewer 做引用验证（避免超长；
        # 与 coordinator 传入的截断上限保持一致）
        materials_truncated = (research_materials or "")[:8000]
        if not materials_truncated.strip():
            materials_truncated = "（无原文片段提供）"

        prompt = REVIEW_PROMPT.format(
            query=query,
            report=report,
            research_materials=materials_truncated,
        )
        response = call_llm(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        parsed = self._parse_review(response)
        if not parsed:
            # LLM 不可用或输出无法解析 → 默认不通过（质量门禁静默洞开的历史 bug）
            result = {
                "passed": False,
                "score": 0,
                "failure_type": "other",
                "feedback": "审核结果解析失败或 LLM 不可用，按不通过处理",
            }
        else:
            result = parsed
        result = self._ensure_fields(result)

        if result["passed"]:
            logger.info(f"  [Reviewer] 评分: {result['score']}/10, 通过")
        else:
            logger.warning(f"  [Reviewer] 评分: {result['score']}/10, 不通过"
                           f" (类型: {result['failure_type']})")

        return result

    @staticmethod
    def _parse_review(response: str) -> dict:
        """解析 LLM 返回的首个 JSON 对象（括号平衡解析，容忍尾随文字）"""
        if not response:
            return {}
        start = response.find("{")
        if start < 0:
            return {}
        try:
            obj, _ = json.JSONDecoder().raw_decode(response[start:])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _ensure_fields(result: dict) -> dict:
        """确保返回字段完整"""
        defaults = {
            "passed": True,
            "score": 8,
            "failure_type": "",
            "feedback": "审核通过",
            "suggestions": [],
            "missing_entities": [],
            "retrieval_hints": [],
        }
        defaults.update(result)
        # 通过时清理分类
        if defaults["passed"]:
            defaults["failure_type"] = ""
        # 不通过时必须有分类
        elif not defaults["failure_type"]:
            defaults["failure_type"] = "other"
        return defaults
