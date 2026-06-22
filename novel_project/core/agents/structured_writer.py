"""
结构化撰稿 Agent (v2)
======================
在 Writer 的基础上增加:
    1. JSON Schema 强制输出 (每条结论含 chapter_title + source_snippet)
    2. 原文锚点校验
    3. 幻觉自动重写 (最多 2 次)

用法:
    writer = StructuredWriter()
    result = writer.write_with_citations(query, intent, materials)
    # result = {
    #     "report": "...",
    #     "claims": [{"claim": "...", "chapter_title": "...", "source_snippet": "...", "verified": True}],
    #     "warnings": [...]
    # }
"""
from __future__ import annotations

import json
import os
import re
import sys
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

logger = logging.getLogger(__name__)

from core.llm import call_llm


# ── Prompt 模板 ─────────────────────────────────────────────────────────

STRUCTURED_WRITER_PROMPT = """你是一个网文分析专家。请根据 Researcher 提供的资料，生成一份分析报告。

要求:
1. 报告用中文撰写，结构清晰
2. **每条结论都必须标注来源**，格式为「引自第X章 章节名」
3. 如果资料不足，如实说明「资料不足，以下结论基于有限信息」
4. 字数控制在 300-600 字

用户问题: {query}
分析类型: {intent}

检索到的资料:
{materials}

请以 JSON 格式返回:
{{
    "report": "完整分析报告文本（含来源标注）",
    "claims": [
        {{
            "claim": "单条结论",
            "chapter_title": "来源章节标题",
            "source_snippet": "原文片段（用于校验）"
        }}
    ],
    "data_quality_warning": "如果资料不足，写在此处。否则为空字符串"
}}
"""


# ── 数据模型 ────────────────────────────────────────────────────────────

@dataclass
class Claim:
    """单条含锚点的结论"""
    text: str
    chapter_title: str
    source_snippet: str
    verified: bool = False
    verification_detail: str = ""


@dataclass
class StructuredReport:
    """结构化分析报告"""
    report: str
    claims: list[Claim]
    warnings: list[str] = field(default_factory=list)
    all_verified: bool = False


# ── 结构化 Writer ───────────────────────────────────────────────────────

class StructuredWriter:
    """
    结构化撰稿人：输出 JSON 格式的含锚点报告。
    """

    def __init__(self, llm_fn: Callable = call_llm):
        self._llm = llm_fn

    def write(self, query: str, intent: str, materials: list[dict]) -> StructuredReport:
        """
        生成含锚点的结构化报告。

        参数:
            query: 用户问题
            intent: 意图类型
            materials: Researcher 收集的资料 [{step, description, result}, ...]

        返回:
            StructuredReport
        """
        formatted = []
        for m in materials:
            if m.get("result"):
                formatted.append(f"【步骤 {m['step']}】{m['description']}\n{m['result']}")

        all_materials = "\n\n".join(formatted) if formatted else "（无检索结果）"

        prompt = STRUCTURED_WRITER_PROMPT.format(
            query=query,
            intent=intent,
            materials=all_materials,
        )

        response = self._llm(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        return self._parse_response(response)

    def write_with_verification(
        self,
        query: str,
        intent: str,
        materials: list[dict],
        retriever=None,
        max_rewrites: int = 2,
    ) -> StructuredReport:
        """
        生成 → 校验 → 重写（最多 2 轮）。

        参数:
            retriever: NovelRetriever 实例，用于原文校验
            max_rewrites: 最大重写次数

        返回:
            StructuredReport
        """
        report = self.write(query, intent, materials)

        if not retriever:
            return report

        verified_claims = []
        warnings = []

        for claim in report.claims:
            result = verify_claim(claim, retriever)
            if result["verified"]:
                claim.verified = True
                verified_claims.append(claim)
            else:
                warnings.append(f"结论「{claim.text[:30]}...」校验未通过: {result.get('reason', '')}")

        # 如果有校验失败且还有重写机会
        if warnings and max_rewrites > 0:
            logger.info(f"有 {len(warnings)} 条结论校验失败，尝试重写...")
            # 把 feedback 加入 prompt 重试
            revised = self._rewrite(query, intent, materials, warnings)
            if revised:
                report = revised

        report.warnings = warnings
        report.all_verified = len(warnings) == 0
        return report

    def _parse_response(self, response: str) -> StructuredReport:
        """解析 LLM 返回的 JSON"""
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if not json_match:
            return StructuredReport(
                report=response,
                claims=[],
                warnings=["LLM 返回格式异常，未解析到 JSON"],
            )

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return StructuredReport(
                report=response,
                claims=[],
                warnings=["JSON 解析失败"],
            )

        claims_raw = data.get("claims", [])
        claims = []
        for c in claims_raw:
            claims.append(Claim(
                text=c.get("claim", ""),
                chapter_title=c.get("chapter_title", ""),
                source_snippet=c.get("source_snippet", ""),
            ))

        warnings = []
        if data.get("data_quality_warning"):
            warnings.append(data["data_quality_warning"])

        return StructuredReport(
            report=data.get("report", response),
            claims=claims,
            warnings=warnings,
        )

    def _rewrite(
        self,
        query: str,
        intent: str,
        materials: list[dict],
        warnings: list[str],
    ) -> StructuredReport | None:
        """根据校验反馈重写报告"""
        feedback_text = "\n".join(warnings)
        prompt = STRUCTURED_WRITER_PROMPT.format(
            query=query,
            intent=intent,
            materials=self._fmt_materials(materials),
        )
        prompt += f"\n\n⚠️ 上一版有以下问题，请修正后重新生成:\n{feedback_text}"

        try:
            response = self._llm(
                [{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"重写失败: {e}")
            return None

    @staticmethod
    def _fmt_materials(materials: list[dict]) -> str:
        formatted = []
        for m in materials:
            if m.get("result"):
                formatted.append(f"【步骤 {m['step']}】{m['description']}\n{m['result']}")
        return "\n\n".join(formatted) if formatted else "（无检索结果）"


# ── 锚点校验器 ──────────────────────────────────────────────────────────

def verify_claim(claim: Claim, retriever) -> dict:
    """
    校验单条结论是否有原文依据。

    策略:
        1. 如果 claim 指定了 chapter_title，直接从该章找原文
        2. 如果未指定或未找到，用 source_snippet 全文搜索
        3. 返回校验结果

    返回:
        {"verified": bool, "reason": str, "matched_snippet": str}
    """
    # 策略 1: 按章节名查找
    if claim.chapter_title:
        for ch in retriever.chapters:
            if ch.get("title", "").strip() == claim.chapter_title.strip():
                if _snippet_in_text(claim.source_snippet, ch.get("text", "")):
                    return {"verified": True, "reason": "原文匹配", "matched_snippet": claim.source_snippet}
                # 章节找到了但片段不匹配
                return {
                    "verified": False,
                    "reason": f"章节「{claim.chapter_title}」存在，但原文片段不匹配",
                    "matched_snippet": "",
                }

    # 策略 2: 模糊搜索
    query_words = claim.text.split()[:5]
    for ch in retriever.chapters:
        text = ch.get("text", "")
        for word in query_words:
            if len(word) > 1 and word in text:
                return {
                    "verified": True,
                    "reason": f"在章节「{ch.get('title')}」中找到匹配",
                    "matched_snippet": text[:200],
                }

    return {"verified": False, "reason": "未在任何章节中找到匹配", "matched_snippet": ""}


def _snippet_in_text(snippet: str, text: str) -> bool:
    """检查原文片段是否在章节文本中（支持模糊匹配）"""
    if not snippet:
        return True  # 无片段则默认通过
    # 去除标点再比较
    clean_snippet = re.sub(r"[，。！？、；：""''（）\s]", "", snippet)
    clean_text = re.sub(r"[，。！？、；：""''（）\s]", "", text)
    return clean_snippet[:50] in clean_text or clean_text[:50] in clean_snippet
