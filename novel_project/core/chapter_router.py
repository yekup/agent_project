"""
章节复杂度分级路由 + 语义去重
=================================
核心逻辑:
    1. 分析每章文本: 实体密度、对话占比、情节新信息量
    2. 动态分级: PRO(全量LLM) / LIGHT(摘要复用) / SKIP(跳过)
    3. SimHash + MinHash 段落去重，避免重复内容浪费 Token

用法:
    router = ChapterRouter()
    tier = router.route_chapter(chapter_text)  # 返回 ModelTier

    deduper = TextDeduplicator()
    is_dup = deduper.is_duplicate(text)         # 返回 bool
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模型等级
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    """模型等级"""
    PRO = "pro"       # 云端高端模型 (DeepSeek V4) —— 高信息密度章节
    LIGHT = "light"   # 低成本模型 (可省略自定义配置) —— 普通章节
    SKIP = "skip"     # 跳过 —— 低信息量/模板化章节，复用已有摘要


# ---------------------------------------------------------------------------
# 章节复杂度分析
# ---------------------------------------------------------------------------

@dataclass
class ChapterComplexity:
    """单章复杂度指标"""
    entity_density: float = 0.0       # 实体密度 (人名/地名出现的频率)
    dialogue_ratio: float = 0.0       # 对话占比
    novelty_score: float = 0.0        # 新信息量 (与前文对比)
    paragraph_count: int = 0
    total_chars: int = 0
    tier: ModelTier = ModelTier.LIGHT
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "entity_density": round(self.entity_density, 4),
            "dialogue_ratio": round(self.dialogue_ratio, 4),
            "novelty_score": round(self.novelty_score, 4),
            "paragraph_count": self.paragraph_count,
            "total_chars": self.total_chars,
            "tier": self.tier.value,
            "score": round(self.score, 4),
        }


class ChapterRouter:
    """
    章节复杂度分级路由。

    根据实体密度、对话占比等指标计算复杂度分数，将章节分入 PRO/LIGHT/SKIP 三级。

    阈值自适应: 前 CALIBRATION_CHAPTERS 章作为校准样本，
                动态计算该书专属的百分位阈值。

    用法:
        router = ChapterRouter()
        for chapter in chapters:
            tier = router.route_chapter(chapter["text"])
            if tier == ModelTier.SKIP:
                continue  # 复用已有摘要
            elif tier == ModelTier.LIGHT:
                # 使用精简 prompt
            else:
                # 使用全量 prompt
    """

    CALIBRATION_CHAPTERS = 10  # 用于校准的章节数

    # 默认百分位阈值（会被校准覆盖）
    DEFAULT_PRO_PERCENTILE = 80    # 前 20% 的章节用 PRO
    DEFAULT_SKIP_PERCENTILE = 30   # 后 30% 的章节跳过

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._pro_threshold = self.config.get("pro_threshold", 0.6)
        self._skip_threshold = self.config.get("skip_threshold", 0.2)
        self._calibrated = False
        self._calibration_scores: list[float] = []

    def route_chapter(self, chapter_text: str) -> ModelTier:
        """对单章分级"""
        complexity = self.analyze(chapter_text)
        return complexity.tier

    def analyze(self, chapter_text: str) -> ChapterComplexity:
        """分析章节复杂度"""
        c = ChapterComplexity()
        c.total_chars = len(chapter_text)
        c.paragraph_count = len(chapter_text.split("\n"))

        # 1. 实体密度: (人名+地名) / 总字符数
        # 简单实现: 统计引号外的中文字符中，连续 2-4 字的组合出现频率
        names = self._extract_potential_names(chapter_text)
        c.entity_density = len(names) / max(c.total_chars, 1) * 1000  # 每千字实体数

        # 2. 对话占比: 引号内字符 / 总字符
        # 支持「」『』"" 三种引号
        dialogue_matches = re.findall(
            r'[「][^」]*[」]|[『][^』]*[』]|"[^"]*"',
            chapter_text
        )
        dialogue_chars = len("".join(dialogue_matches))
        c.dialogue_ratio = dialogue_chars / max(c.total_chars, 1)

        # 3. 综合评分
        score = 0.6 * min(c.entity_density / 5.0, 1.0) \
              + 0.3 * c.dialogue_ratio \
              + 0.1 * min(c.paragraph_count / 50.0, 1.0)
        c.score = score

        # 分级决策
        if self._calibrated:
            c.tier = self._classify_calibrated(score)
        else:
            c.tier = self._classify_default(score)

        return c

    def calibrate(self, chapters: list[str]) -> None:
        """
        用前 N 章校准阈值。

        取前 CALIBRATION_CHAPTERS 章，计算每章分数，
        然后按百分位设定 PRO/SKIP 阈值。
        """
        scores = []
        for ch in chapters[:self.CALIBRATION_CHAPTERS]:
            c = self.analyze(ch)
            scores.append(c.score)

        if not scores:
            return

        scores.sort()
        n = len(scores)
        pro_idx = max(1, int(n * self.DEFAULT_PRO_PERCENTILE / 100) - 1)
        skip_idx = max(0, int(n * self.DEFAULT_SKIP_PERCENTILE / 100) - 1)

        self._pro_threshold = scores[min(pro_idx, n - 1)]
        self._skip_threshold = scores[skip_idx]
        self._calibrated = True
        self._calibration_scores = scores

        logger.info(
            f"[ChapterRouter] 校准完成: "
            f"pro_threshold={self._pro_threshold:.3f}, "
            f"skip_threshold={self._skip_threshold:.3f}, "
            f"based on {n} chapters"
        )

    # ── 私有方法 ──────────────────────────────────────────────────

    def _classify_default(self, score: float) -> ModelTier:
        if score >= self._pro_threshold:
            return ModelTier.PRO
        elif score <= self._skip_threshold:
            return ModelTier.SKIP
        return ModelTier.LIGHT

    def _classify_calibrated(self, score: float) -> ModelTier:
        return self._classify_default(score)

    @staticmethod
    def _extract_potential_names(text: str) -> list[str]:
        """
        简单提取潜在人名: 2-4 字的中文组合，出现在"说/道/喊道"前
        或引号外的专有名词。
        """
        names = set()
        # 模式 1: XXX说/道/喊道/问
        for m in re.finditer(r"([一-鿿]{2,4})(?:说|道|喊道|问|答|笑)", text):
            names.add(m.group(1))
        # 模式 2: 引号外的连续中文字符（排除常见虚词）
        text_no_quote = re.sub(r"「[^」]*」|『[^』]*』|\"[^\"]*\"", "", text)
        common_words = {"什么", "如何", "可以", "没有", "我们", "他们", "大家",
                         "一个", "这个", "那个", "自己", "起来", "时候", "就是"}
        for m in re.finditer(r"[一-鿿]{2,4}", text_no_quote):
            word = m.group()
            if word not in common_words:
                names.add(word)
        return list(names)


# ---------------------------------------------------------------------------
# SimHash 语义去重
# ---------------------------------------------------------------------------

class SimHashDeduplicator:
    """
    SimHash 局部敏感哈希 —— 检测近似重复段落。

    阈值说明:
        - hamming_dist <= 3: 高度相似 (Jaccard > 0.85) → 判定为重复
        - hamming_dist <= 6: 中度相似
        - hamming_dist > 6:  不相似
    """

    def __init__(self, threshold: int = 3, hash_bits: int = 64):
        self.threshold = threshold
        self.hash_bits = hash_bits
        self._seen_hashes: list[int] = []

    def is_duplicate(self, text: str) -> bool:
        """检查文本是否与已见过的文本重复"""
        h = self._simhash(text)
        for seen_hash in self._seen_hashes:
            if self._hamming_distance(h, seen_hash) <= self.threshold:
                return True
        self._seen_hashes.append(h)
        return False

    def is_duplicate_batch(self, texts: list[str]) -> list[bool]:
        """批量检查，返回每个文本是否重复"""
        results = []
        for text in texts:
            results.append(self.is_duplicate(text))
        return results

    def reset(self):
        """重置去重状态（换书时调用）"""
        self._seen_hashes.clear()

    # ── SimHash 算法 ─────────────────────────────────────────────

    def _simhash(self, text: str) -> int:
        """计算 SimHash 值"""
        tokens = self._tokenize(text)
        v = [0] * self.hash_bits

        for token in tokens:
            h = self._string_hash(token)
            for i in range(self.hash_bits):
                bit = (h >> i) & 1
                if bit:
                    v[i] += 1
                else:
                    v[i] -= 1

        fingerprint = 0
        for i in range(self.hash_bits):
            if v[i] > 0:
                fingerprint |= (1 << i)

        return fingerprint

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """文本分片为 4-gram 特征"""
        text = re.sub(r"\s+", "", text)
        if len(text) < 4:
            return [text]
        return [text[i:i+4] for i in range(len(text) - 3)]

    @staticmethod
    def _string_hash(s: str) -> int:
        """字符串转整数哈希"""
        return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)

    @staticmethod
    def _hamming_distance(x: int, y: int) -> int:
        """计算汉明距离"""
        return bin(x ^ y).count("1")


# ---------------------------------------------------------------------------
# MinHash 精确去重（补充 SimHash 的误报）
# ---------------------------------------------------------------------------

class MinHashDeduplicator:
    """
    MinHash 用于更精确的段落去重。

    SimHash 先做粗筛（可能有误报），MinHash 做二次确认。

    用法:
        deduper = MinHashDeduplicator(threshold=0.85)
        if deduper.is_duplicate("新段落"):
            # 跳过
    """

    def __init__(self, threshold: float = 0.85, num_perm: int = 128):
        self.threshold = threshold
        self._seen: list[Any] = []

    def is_duplicate(self, text: str) -> bool:
        """判断文本是否与已有文本重复 (Jaccard > threshold)"""
        try:
            from datasketch import MinHash
        except ImportError:
            logger.warning("datasketch 未安装，MinHash 去重跳过")
            return False

        mh = MinHash(num_perm=128)
        tokens = self._tokenize(text)
        for t in tokens:
            mh.update(t.encode("utf-8"))

        for seen_mh in self._seen:
            jaccard = mh.jaccard(seen_mh)
            if jaccard > self.threshold:
                return True

        self._seen.append(mh)
        return False

    def reset(self):
        self._seen.clear()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """4-gram 分片"""
        text = re.sub(r"\s+", "", text)
        if len(text) < 4:
            return [text]
        return [text[i:i+4] for i in range(len(text) - 3)]


# ---------------------------------------------------------------------------
# 组合去重器
# ---------------------------------------------------------------------------

class TextDeduplicator:
    """
    组合去重器: SimHash 粗筛 → MinHash 确认。

    性能:
        SimHash O(1) 查询，MinHash O(n) 确认。
        对于千章级小说，SimHash 先过滤掉 90% 的明显重复，
        只有边界情况才走 MinHash。

    用法:
        deduper = TextDeduplicator()
        for chapter in chapters:
            for para in chapter["paragraphs"]:
                if deduper.is_duplicate(para):
                    continue  # 跳过重复段落
                # 处理新段落
    """

    def __init__(
        self,
        simhash_threshold: int = 3,
        minhash_threshold: float = 0.85,
    ):
        self._simhash = SimHashDeduplicator(threshold=simhash_threshold)
        self._minhash = MinHashDeduplicator(threshold=minhash_threshold)
        self.stats = {"simhash_hits": 0, "minhash_checks": 0, "total_checked": 0}

    def is_duplicate(self, text: str) -> bool:
        """
        两步去重:
            1. SimHash 快速判断
            2. 边界情况 (hamming == 3) 走 MinHash 二次确认
        """
        self.stats["total_checked"] += 1

        # 先走 SimHash
        if self._simhash.is_duplicate(text):
            self.stats["simhash_hits"] += 1
            return True

        # 如果 SimHash 不判定为重复，再走 MinHash 兜底
        # （SimHash 偏保守，MinHash 更精确）
        self.stats["minhash_checks"] += 1
        return self._minhash.is_duplicate(text)

    def reset(self):
        """重置（换书时调用）"""
        self._simhash.reset()
        self._minhash.reset()
        self.stats = {"simhash_hits": 0, "minhash_checks": 0, "total_checked": 0}

    def print_stats(self):
        """打印去重统计"""
        logger.info(
            f"[TextDeduplicator] 总计检查: {self.stats['total_checked']}, "
            f"SimHash 命中: {self.stats['simhash_hits']}, "
            f"MinHash 二次确认: {self.stats['minhash_checks']}"
        )


# ── 整合编译管道 ────────────────────────────────────────────────────────

class SmartChapterPipeline:
    """
    智能编译管道: 分级路由 + 去重 + 成本追踪。

    使用方式:
        pipeline = SmartChapterPipeline()
        results = pipeline.process(novel_data)

    这替换了原有的 chapter_parser.py 中的 build_wiki 中的简单循环。
    """

    def __init__(
        self,
        router: ChapterRouter | None = None,
        deduplicator: TextDeduplicator | None = None,
        config: dict | None = None,
    ):
        self.router = router or ChapterRouter(config)
        self.dedup = deduplicator or TextDeduplicator()
        self.config = config or {}

        # 预算追踪
        self.cost_tracker = CostTracker()

    def process(self, novel_data: dict) -> list[dict]:
        """
        处理整本小说:
            1. 用前 10 章校准阈值
            2. 逐章分级 + 去重
            3. 返回处理结果

        返回:
            [{"chapter_index": int, "chapter_title": str, "tier": str, "skip_reason": str, ...}]
        """
        chapters = novel_data.get("chapters", [])
        title = novel_data.get("title", "")

        # 1. 校准
        texts = [ch["text"] for ch in chapters]
        self.router.calibrate(texts)

        results = []
        for i, ch in enumerate(chapters):
            tier = self.router.route_chapter(ch["text"])
            is_dup = self.dedup.is_duplicate(ch["text"][:500])

            entry = {
                "chapter_index": i,
                "chapter_title": ch["title"],
                "tier": tier.value,
                "is_duplicate": is_dup,
                "skip_reason": "",
            }

            if is_dup:
                entry["skip_reason"] = "语义重复"
                entry["tier"] = ModelTier.SKIP.value
            elif tier == ModelTier.SKIP:
                entry["skip_reason"] = "低信息量章节"

            # 追踪成本
            tier_cost = {"pro": 1.0, "light": 0.3, "skip": 0.0}
            self.cost_tracker.add_chapter(
                tier=tier.value,
                estimated_cost=tier_cost.get(tier.value, 0),
                char_count=len(ch["text"]),
            )

            results.append(entry)

        logger.info(
            f"[SmartChapterPipeline] 《{title}》处理完成: "
            f"{len(results)} 章, "
            f"PRO={sum(1 for r in results if r['tier']=='pro')}, "
            f"LIGHT={sum(1 for r in results if r['tier']=='light')}, "
            f"SKIP={sum(1 for r in results if r['tier']=='skip')}, "
            f"去重跳过={sum(1 for r in results if r['is_duplicate'])}"
        )
        return results


# ---------------------------------------------------------------------------
# 成本追踪器
# ---------------------------------------------------------------------------

class CostTracker:
    """
    编译成本追踪。

    实时统计:
        - 已消耗 Token (估计)
        - 已消耗金额
        - 各 tier 分布
        - 预估全书总成本

    与前端看板集成 (v2.0 §3.3.2):
        GET /api/cost?novel=xxx
    """

    # 粗略估价（每 1K token）
    ESTIMATED_COST_PER_1K = {
        "pro": 0.002,    # $0.002/1K tokens (DeepSeek)
        "light": 0.0005, # $0.0005/1K
        "skip": 0.0,
    }

    # 每章平均 Token 消耗（估计，含 prompt + completion）
    ESTIMATED_TOKENS_PER_CHAPTER = 4000

    def __init__(self, budget_usd: float | None = None):
        self.budget_usd = budget_usd  # 预算上限（美元）
        self.chapters_processed: list[dict] = []

    def add_chapter(self, tier: str, estimated_cost: float, char_count: int):
        """记录已处理的章节"""
        self.chapters_processed.append({
            "tier": tier,
            "estimated_cost": estimated_cost,
            "char_count": char_count,
        })

    @property
    def total_cost(self) -> float:
        """总消耗（美元）"""
        return sum(c["estimated_cost"] for c in self.chapters_processed)

    @property
    def total_estimated_tokens(self) -> int:
        """预估总 Token 数"""
        return len(self.chapters_processed) * self.ESTIMATED_TOKENS_PER_CHAPTER

    @property
    def budget_remaining(self) -> float:
        """剩余预算"""
        if self.budget_usd is None:
            return float("inf")
        return max(0.0, self.budget_usd - self.total_cost)

    @property
    def is_budget_exhausted(self) -> bool:
        """预算是否耗尽"""
        if self.budget_usd is None:
            return False
        return self.total_cost >= self.budget_usd

    def get_report(self) -> dict:
        """生成成本报告（给前端看板用）"""
        tiers = {}
        for c in self.chapters_processed:
            t = c["tier"]
            tiers[t] = tiers.get(t, 0) + 1

        return {
            "total_chapters": len(self.chapters_processed),
            "tier_distribution": tiers,
            "total_estimated_cost_usd": round(self.total_cost, 4),
            "total_estimated_tokens": self.total_estimated_tokens,
            "budget_usd": self.budget_usd,
            "budget_remaining_usd": round(self.budget_remaining, 4),
            "is_budget_exhausted": self.is_budget_exhausted,
        }

    def print_report(self):
        """打印成本报告"""
        r = self.get_report()
        lines = [
            "\n" + "=" * 50,
            "  编译成本报告",
            "=" * 50,
            f"  总章节:          {r['total_chapters']}",
            f"  各级分布:        {r['tier_distribution']}",
            f"  预估 Token:      {r['total_estimated_tokens']:,}",
            f"  预估成本:        ${r['total_estimated_cost_usd']:.4f}",
        ]
        if r["budget_usd"]:
            lines.append(f"  预算:            ${r['budget_usd']:.2f}")
            lines.append(f"  剩余:            ${r['budget_remaining_usd']:.4f}")
            lines.append(f"  是否耗尽:        {'是' if r['is_budget_exhausted'] else '否'}")
        lines.append("=" * 50)
        print("\n".join(lines))


# ── 测试 ────────────────────────────────────────────────────────────────

def demo():
    """在绍宋数据上演示分级路由"""
    import json
    with open("data/wiki/shaosong_hierarchical.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = data.get("chapters", [])
    texts = [ch["text"] for ch in chapters if ch.get("text")]

    # 如果没有 text 字段，用 summary 模拟
    if not texts:
        texts = [ch.get("summary", "") for ch in chapters]

    # 示例
    ch = chapters[0]
    router = ChapterRouter()
    tier = router.route_chapter(ch.get("text", ch.get("summary", "")))
    print(f"章节「{ch.get('chapter_title')}」路由结果: {tier.value}")

    # 校准后重试
    router.calibrate(texts)
    tier = router.route_chapter(ch.get("text", ch.get("summary", "")))
    print(f"校准后: {tier.value}")

    # 去重测试
    deduper = TextDeduplicator()
    test_texts = ["本章完本章完本章完本章完", "求月票求月票求月票", "第一章 穿越"]
    for t in test_texts:
        print(f"去重判断「{t[:20]}」: {deduper.is_duplicate(t)}")

    deduper.print_stats()

    # 成本追踪
    tracker = CostTracker(budget_usd=5.0)
    pipeline = SmartChapterPipeline(cost_tracker=tracker)
    results = pipeline.process({"chapters": [{"title": "第1章", "text": "aaa"}, {"title": "第2章", "text": "bbb"}], "title": "测试"})
    tracker.print_report()


if __name__ == "__main__":
    demo()
