"""
中文文本匹配工具
================
中文没有空格分词，`str.split()` 会把整段文本当成一个"词"，
导致 `word in query` 这类英文式关键词匹配对中文几乎全部失效
（历史上 Wiki 检索的标题/摘要/事件三维打分因此形同虚设）。

本模块提供基于字符 n-gram 的包含匹配，供检索链路统一使用。
"""
from __future__ import annotations

import re

_PUNCT_RE = re.compile(r"[\s，。！？、；：（）()【】《》〈〉「」『』\"'.,!?;:\-—…·]+")

# 章节号前缀：第一章 / 第12回 / 第三集 ...
_CHAPTER_PREFIX_RE = re.compile(r"^第[零一二两三四五六七八九十百千\d]+[章回集节]\s*")


def char_ngrams(text: str, n: int = 2) -> list[str]:
    """按标点切分后生成字符 n-gram 序列"""
    tokens: list[str] = []
    for seg in _PUNCT_RE.split(text or ""):
        if not seg:
            continue
        if len(seg) < n:
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + n] for i in range(len(seg) - n + 1))
    return tokens


def ngram_hits(query: str, text: str, n: int = 2) -> int:
    """query 的去重 n-gram 在 text 中的命中数（0 表示无相关）"""
    if not query or not text:
        return 0
    return sum(1 for g in set(char_ngrams(query, n)) if g in text)


def chapter_title_core(title: str) -> str:
    """去掉章节标题的「第X章」前缀，返回核心名（如 '第一章 明道宫' → '明道宫'）"""
    return _CHAPTER_PREFIX_RE.sub("", title or "").strip()
