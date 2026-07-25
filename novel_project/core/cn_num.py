"""
中文数字解析
============
统一处理「十/百/千」乘数规则的中文数字 → int 转换。

历史背景：项目里曾有 4 处各自实现的中文数字解析，都把「十一」解析成 1、
「二十」解析成 2（按字典逐字累加），导致章节定位、引用校验全部错位。
"""
from __future__ import annotations

import re

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 章节号提取：第X章 / 第X回 / 第X集 / 第X节（X 为中文或阿拉伯数字）
CHAPTER_NUM_RE = re.compile(r"第\s*([零一二两三四五六七八九十百千\d]+)\s*[章回集节]")


def chinese_to_int(s: str) -> int | None:
    """
    将中文数字串（'十一'/'二十'/'一百零五'）或阿拉伯数字串转为 int。
    无法解析时返回 None。

    规则:
      - '十' 前无数字视为 1×10（'十一' → 11）
      - 单位字作乘数累计（'三百二十' → 320）
    """
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)

    total = 0
    section = 0
    for ch in s:
        if ch in _CN_DIGITS:
            section = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            section = max(section, 1) * _CN_UNITS[ch]
            total += section
            section = 0
        elif ch.isdigit():
            section = section * 10 + int(ch)
        else:
            return None
    return total + section


def extract_chapter_number(text: str) -> int | None:
    """从文本中提取「第X章」的章节号，如 '引自第十一章 明道宫' → 11"""
    m = CHAPTER_NUM_RE.search(text or "")
    if not m:
        return None
    return chinese_to_int(m.group(1))


def find_chapter_by_number(chapters: list[dict], ch_num: int) -> dict | None:
    """
    按章节标题中的编号定位章节。

    优先匹配标题里的「第X章」编号（兼容前言/楔子等前置内容造成的
    位置偏移）；没有任何标题编号时退回位置索引（ch_num 为 1-based）。
    """
    if not ch_num or ch_num < 1:
        return None
    for ch in chapters:
        title = ch.get("title", ch.get("chapter_title", ""))
        if extract_chapter_number(title) == ch_num:
            return ch
    # 位置兜底：数据无标题编号时按顺序取
    if ch_num <= len(chapters):
        return chapters[ch_num - 1]
    return None
