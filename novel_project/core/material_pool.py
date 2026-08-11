"""
分段式材料管理 (MaterialPool)
==============================
解决多轮问答中上下文不断膨胀的问题。

策略:
    第1轮: 所有材料完整保留（首次生成需要全量信息）
    第2轮起: 旧材料用 LLM 摘要压缩到 1/5，只保留关键结论
    始终只保留最近 2 轮 + 压缩后的历史摘要

使用:
    pool = MaterialPool()
    pool.add_round(materials)           # 添加一轮材料
    effective = pool.get_effective()     # 获取压缩后的 prompt 文本
    pool.get_prompt_for_writer(query, intent)  # 一键获取完整 prompt
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.llm import call_llm


@dataclass
class MaterialRound:
    """一轮检索材料"""
    round_num: int
    materials: list[dict]
    summary: str = ""           # 本轮的 LLM 摘要
    summary_short: str = ""     # 极简摘要（50字）
    timestamp: float = 0.0

    @property
    def text(self) -> str:
        """完整文本"""
        formatted = []
        for m in self.materials:
            if m.get("result"):
                formatted.append(
                    f"【步骤 {m['step']}】{m['description']}\n{m['result']}"
                )
        return "\n\n".join(formatted)

    @property
    def char_count(self) -> int:
        return len(self.text)


class MaterialPool:
    """
    分段式材料池。

    用法:
        pool = MaterialPool(llm_compress=True)  # 启用 LLM 压缩
        pool.add_round(materials)               # 每轮结束后添加
        prompt = pool.get_prompt_for_writer(query, intent)
    """

    COMPRESS_PROMPT = """请将以下检索资料压缩为一段 200 字以内的简洁摘要，保留关键结论和核心事实：

{text}

只返回摘要文本，不要加其他文字。"""

    def __init__(self, llm_compress: bool = True, max_rounds: int = 3):
        self._rounds: list[MaterialRound] = []
        self._llm_compress = llm_compress
        self._max_rounds = max_rounds
        self._global_summary: str = ""  # 跨轮压缩摘要
        self._archived: list[str] = []  # 被丢弃轮次的极简摘要（防信息静默丢失）

    # ── 核心接口 ──────────────────────────────────────────────────

    def add_round(self, materials: list[dict]):
        """添加一轮检索材料"""
        mr = MaterialRound(
            round_num=len(self._rounds) + 1,
            materials=materials,
            timestamp=time.time(),
        )
        self._rounds.append(mr)

        # 延迟压缩：压缩"已成历史"的上一轮，而不是当前轮。
        # 单轮流程（多数问答一次通过）里当前轮的摘要永远不会被读到，
        # 每轮白付 2 次 LLM 调用（实测占研究阶段 15-25s）。
        if self._llm_compress and len(self._rounds) >= 2:
            prev = self._rounds[-2]
            if prev.text and not prev.summary:
                prev.summary = self._summarize(prev.text)
                prev.summary_short = self._summarize_short(prev.text)
            # 合并到全局摘要
            sources = [r.summary_short for r in self._rounds if r.summary_short]
            if len(sources) >= 2:
                combined = "\n".join(sources)
                if len(combined) > 300:
                    self._global_summary = self._summarize_short(combined)

        # 超出轮数限制时丢弃最旧轮（归档前补算极简摘要，不丢结论）
        while len(self._rounds) > self._max_rounds:
            dropped = self._rounds.pop(0)
            if self._llm_compress and dropped.text and not dropped.summary_short:
                dropped.summary_short = self._summarize_short(dropped.text)
            if dropped.summary_short:
                self._archived.append(f"第{dropped.round_num}轮: {dropped.summary_short}")

    def get_effective(self) -> str:
        """
        获取构建 prompt 用的材料文本。

        第1轮: 完整材料
        第2轮起: 全局摘要 + 最近 2 轮全量
        """
        if len(self._rounds) <= 1:
            return self._rounds[-1].text if self._rounds else "(无资料)"

        parts = []
        # 全局摘要 + 已归档的早期轮摘要
        if self._global_summary or self._archived:
            confirmed = "\n".join(filter(None, [self._global_summary, *self._archived]))
            parts.append(f"【前期已确认信息】\n{confirmed}")

        # 最近 2 轮全量
        recent = self._rounds[-2:]
        for r in recent:
            if r.summary_short:
                parts.append(
                    f"【第{r.round_num}轮检索摘要】\n{r.summary_short}"
                )
            parts.append(r.text)

        return "\n\n".join(parts)

    def get_prompt_for_writer(self, query: str, intent: str) -> str:
        """直接生成 Writer 可用的 prompt"""
        materials = self.get_effective()

        prompt = f"""你是一个网文分析专家。请根据 Researcher 提供的资料，写一份分析报告。

用户问题：{query}
分析类型：{intent}

检索到的资料：
{materials}

要求：
1. 报告结构清晰，使用标题和小标题
2. 引用要标注来源（如「引自第一章 空谈」）
3. 严格基于检索到的资料撰写，不要声称自己"缺乏阅读"或"资料不足"
4. 语言简洁，避免空话套话
5. 字数控制在 500-800 字
"""
        return prompt

    def stats(self) -> dict:
        """统计信息"""
        return {
            "rounds": len(self._rounds),
            "total_chars": sum(r.char_count for r in self._rounds),
            "global_summary_len": len(self._global_summary),
            "has_compression": bool(self._global_summary),
        }

    # ── LLM 压缩 ──────────────────────────────────────────────────

    def _summarize(self, text: str) -> str:
        """完整压缩摘要"""
        if len(text) < 200:
            return text
        try:
            prompt = f"""请将以下资料压缩为 200 字以内的简洁摘要，保留关键事实和结论：

{text[:3000]}

只返回摘要文本，不要加其他文字。"""
            resp = call_llm([{"role": "user", "content": prompt}])
            if not resp:
                return text[:200]
            return resp.strip()[:300]
        except Exception:
            return text[:200]

    def _summarize_short(self, text: str) -> str:
        """极简压缩（50字）"""
        if len(text) < 100:
            return text
        try:
            prompt = f"用一句话概括以下内容（50字以内）：\n{text[:1500]}"
            resp = call_llm([{"role": "user", "content": prompt}])
            if not resp:
                return text[:60]
            return resp.strip()[:80]
        except Exception:
            return text[:60]
