"""
撰稿 Agent
负责：根据 Researcher 收集的资料，撰写分析报告

重要变更 (2026-07-10):
  - 注入实际检索到的章节列表，LLM 只能从该列表中选择引用
  - 生成后自动校验引用的真实性，编造的引用自动清除
  - 当检索材料中没有足够的具体章节时，自动切换为无引用模式
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

from core.llm import call_llm

# 可用章节来源 ≥ 该值时启用章节引用模式；否则无引用模式
MIN_CHAPTER_SOURCES_FOR_CITATION = 3


WRITER_PROMPT_NO_CITATION = """你是一个网文分析专家。请根据 Researcher 提供的资料，概括分析。

用户问题：{query}
分析类型：{intent}

检索到的资料：
{materials}

要求：
1. 报告结构清晰，使用标题和小标题
2. 严格基于检索到的资料撰写，不要声称自己"缺乏阅读"或"资料不足"
3. 语言简洁，避免空话套话
4. 字数控制在 500-800 字
5. 【重要】由于检索材料中未覆盖具体的章节原文，报告中**不要使用任何「引自第X章」的引用格式**，直接概括即可
"""


WRITER_PROMPT = """你是一个网文分析专家。请根据 Researcher 提供的资料，写一份分析报告。

用户问题：{query}
分析类型：{intent}

检索到的资料：
{materials}

【可引用的来源章节】（只能从以下章节中选择引用，不要编造）：
{available_sources}

要求：
1. 报告结构清晰，使用标题和小标题
2. 引用要用「引自《章节名》」格式标注，且必须从【可引用的来源章节】中选择
3. 严禁编造不存在的章节名 —— 只引用上面列出的章节
4. 严格基于检索到的资料撰写，不要声称自己"缺乏阅读"或"资料不足"
5. 语言简洁，避免空话套话
6. 字数控制在 500-800 字
"""


class Writer:
    """撰稿人：基于检索资料撰写分析报告"""

    def write(self, query, intent, materials):
        """
        生成分析报告，自动校验引用真实性。

        智能选择 prompt 模板：
          - 有 >=3 个具体章节来源 -> 章节引用模式（含校验）
          - 有 <3 个具体章节来源 -> 无引用模式（直接概括）

        参数:
            query: 原始问题
            intent: 意图类型
            materials: Researcher 收集的资料列表
                [{step, description, result}, ...]

        返回:
            str: 分析报告文本
        """
        # 1. 格式化资料文本
        formatted = []
        for m in materials:
            if m["result"]:
                formatted.append(f"【步骤 {m['step']}】{m['description']}\n{m['result']}")

        all_materials = "\n\n".join(formatted) if formatted else "（无检索结果）"

        # 2. 提取实际章节名，过滤掉卷级标题
        available_sources = self._extract_chapter_sources(materials)

        # 3. 根据可用来源数量选择 prompt 模板
        if len(available_sources) >= MIN_CHAPTER_SOURCES_FOR_CITATION:
            sources_text = "\n".join(available_sources)
            prompt = WRITER_PROMPT.format(
                query=query,
                intent=intent,
                materials=all_materials,
                available_sources=sources_text,
            )
            report = call_llm([{"role": "user", "content": prompt}])
            if not report:
                return "（LLM 服务暂时不可用，无法生成报告。请检查 API 配置后重试。）"
            report = self._validate_citations(report, available_sources)
            logger.info(f"  [Writer] 报告完成 ({len(report)} 字, {len(available_sources)} 个可引来源, 引用模式)")
        else:
            prompt = WRITER_PROMPT_NO_CITATION.format(
                query=query,
                intent=intent,
                materials=all_materials,
            )
            report = call_llm([{"role": "user", "content": prompt}])
            if not report:
                return "（LLM 服务暂时不可用，无法生成报告。请检查 API 配置后重试。）"
            # 清理可能残留的引用标记
            report, n_removed = re.subn(
                r'「[^」]*(?:引自|出自)[^」]*」', '', report,
            )
            if n_removed > 0:
                logger.info(f"  [Writer] 无引用模式下清除 {n_removed} 个残余引用")
            logger.info(f"  [Writer] 报告完成 ({len(report)} 字, 无引用模式)")

        return report

    # ── 来源提取 ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_chapter_sources(materials: list[dict]) -> list[str]:
        """
        从 Researcher 的检索结果中提取具体章节名。
        过滤掉卷标题（"第1-50章"）和"全书总览"等非具体章节。
        """
        sources = set()

        for m in materials:
            text = m.get("result", "") or ""

            # pattern: 章节：第一章 XXX
            for match in re.finditer(
                r"章节[：:]\s*(第[一-鿿\d一二三四五六七八九十百千零]+[章回][^\n]*)",
                text,
            ):
                sources.add(match.group(1).strip())

            # pattern: [第一章 XXX]
            for match in re.finditer(
                r"[\[【](第[一-鿿\d一二三四五六七八九十百千零]+[章回][^\]】]*)[\]】]",
                text,
            ):
                sources.add(match.group(1).strip())

            # pattern: wiki chapter_title dict
            if isinstance(m.get("result"), dict):
                ch_title = m["result"].get("chapter_title", "")
                if ch_title:
                    sources.add(ch_title)

        # ── 过滤非具体章节条目 ──
        filtered = set()
        for s in sources:
            if re.search(r"第[\d]+[-～~][\d]+[章回]", s):  # "第1-50章"
                continue
            if "全书" in s:
                continue
            if not re.search(r"(第[一-鿿\d]+[章回节])|([\d]+[章回节])", s):
                continue
            filtered.add(s)

        sorted_list = sorted(filtered, key=lambda x: Writer._chapter_sort_key(x))
        return sorted_list

    @staticmethod
    def _chapter_sort_key(name: str) -> int:
        """将章节名转为排序数字"""
        from core.cn_num import chinese_to_int
        match = re.search(r"[一二两三四五六七八九十百千零\d]+", name)
        if match:
            n = chinese_to_int(match.group())
            if n is not None and n > 0:
                return n
        return 999

    # ── 引用校验（严格模式）──────────────────────────────────────────

    @staticmethod
    def _validate_citations(report: str, available_sources: list[str]) -> str:
        """
        校验引用真实性。严格模式：只信任完全匹配和章节号匹配。

        规则:
          1. 「引自第一章 明道宫」中的章节号 → 1
             与 available_sources 中的「第一章 明道宫」章节号 → 1 匹配 → 保留
          2. 「引自第八八章 决战」中的章节号 → 88
             available_sources 中无章节号 88 → 降级为普通文本
        """
        if not available_sources:
            report = re.sub(r'「[^」]*(?:引自|出自)[^」]*」', '', report)
            logger.info(f"  [Writer] 无可用引用，已清除全部引用标记")
            return report

        # ── 构建严格匹配库 ──
        def normalize(name: str) -> str:
            return re.sub(r"[\s　（\)\(）\"\'《》,，。、\-]", "", name).lower()

        def extract_num(name: str) -> int | None:
            """提取章节号: '第一章 明道宫' => 1, '第8章 天理' => 8"""
            from core.cn_num import extract_chapter_number
            return extract_chapter_number(name)

        exact_set = {normalize(s) for s in available_sources}
        num_map = {}
        for s in available_sources:
            n = extract_num(s)
            if n is not None:
                num_map[n] = s

        fake_count = 0
        total_count = 0

        def replace_fake(match):
            nonlocal fake_count, total_count
            total_count += 1
            full_text = match.group(0)
            inner = match.group(1)

            ref_name = re.sub(r"^(?:引自|出自)\s*", "", inner).strip()
            if not ref_name:
                return full_text

            ref_norm = normalize(ref_name)

            # 规则 1: 完全匹配
            if ref_norm in exact_set:
                return full_text

            # 规则 2: 章节号匹配（"第一章" -> 1 匹配 "第1章" -> 1）
            ref_num = extract_num(ref_name)
            if ref_num is not None and ref_num in num_map:
                return full_text

            fake_count += 1
            return f"（{inner}）"

        result = re.sub(r'「([^」]*(?:引自|出自)[^」]*)」', replace_fake, report)

        if fake_count > 0:
            logger.info(f"  [Writer] 校验引用: 清除 {fake_count}/{total_count} 个编造引用")
        else:
            logger.info(f"  [Writer] 引用校验通过 ({total_count} 个引用)")

        return result
