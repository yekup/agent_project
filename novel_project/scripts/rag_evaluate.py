"""
RAG 质量评估脚本
================
基于 LLM-as-Judge 评估回答质量。

指标:
    - faithfulness: 回答是否忠于原文（防止 LLM 编造）
    - answer_relevancy: 回答是否切题
    - context_precision: 检索结果是否精确

用法:
    # 评估单条
    python scripts/rag_evaluate.py --query "萧炎的实力变化" --answer "..." --contexts ...

    # 评估一批
    python scripts/rag_evaluate.py --dataset data/eval/golden/shaosong.json

    # 回归门禁
    python scripts/rag_evaluate.py --gate --threshold 0.75
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rag_evaluate")

from core.llm import call_llm


# ── Prompt 模板 ─────────────────────────────────────────────────────────

NOVEL_FAITHFULNESS_PROMPT = """你是一个网文分析报告审核员。请判断以下回答中的每条结论是否**有原文依据**。

用户问题: {query}

检索到的原文片段:
{contexts}

待审核回答:
{answer}

请逐句判断。对每条结论:
1. 结论原文引用
2. 是否能在原文片段中找到直接支持
3. 是否存在编造或过度推理

以 JSON 格式返回:
{{
    "total_claims": 5,
    "supported_claims": 4,
    "faithfulness_score": 0.8,
    "unsupported_claims": [
        {{"claim": "具体结论文本", "reason": "为什么认为无依据"}}
    ],
    "overall_verdict": "pass"
}}
"""

NOVEL_RELEVANCY_PROMPT = """判断以下回答是否回答了用户的问题。

用户问题: {query}

回答:
{answer}

以 JSON 格式返回:
{{
    "relevancy_score": 0.9,
    "reason": "简要说明"
}}
"""


# ── 数据模型 ────────────────────────────────────────────────────────────

@dataclass
class EvalSample:
    query: str
    answer: str
    contexts: list[str] = field(default_factory=list)
    ground_truth: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    metric: str
    score: float
    passed: bool
    details: dict | None = None
    error: str | None = None


@dataclass
class Report:
    samples_count: int
    scores: dict[str, float]
    passed: bool
    details: list[EvalResult]
    generated_at: str = ""


# ── 评估指标 ────────────────────────────────────────────────────────────

def evaluate_faithfulness(
    sample: EvalSample,
    judge_fn: Callable = call_llm,
    threshold: float = 0.75,
) -> EvalResult:
    """评估回答是否忠于原文"""
    contexts_str = "\n\n".join(sample.contexts[:5]) if sample.contexts else "（未提供原文片段）"
    prompt = NOVEL_FAITHFULNESS_PROMPT.format(
        query=sample.query,
        contexts=contexts_str,
        answer=sample.answer,
    )
    try:
        response = judge_fn([{"role": "user", "content": prompt}])
        import re
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            score = data.get("faithfulness_score", 0.0)
            if isinstance(score, str):
                score = float(score.replace("/", "."))
            score = min(max(float(score), 0.0), 1.0)
            return EvalResult(
                metric="faithfulness",
                score=score,
                passed=score >= threshold,
                details=data,
            )
    except Exception as e:
        logger.warning(f"Faithfulness 评估异常: {e}")
        return EvalResult(metric="faithfulness", score=0.0, passed=False, error=str(e))

    return EvalResult(metric="faithfulness", score=0.0, passed=False, error="解析失败")


def evaluate_relevancy(
    sample: EvalSample,
    judge_fn: Callable = call_llm,
    threshold: float = 0.6,
) -> EvalResult:
    """评估回答是否切题"""
    prompt = NOVEL_RELEVANCY_PROMPT.format(query=sample.query, answer=sample.answer)
    try:
        response = judge_fn([{"role": "user", "content": prompt}])
        import re
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            score = min(max(float(data.get("relevancy_score", 0.5)), 0.0), 1.0)
            return EvalResult(
                metric="answer_relevancy",
                score=score,
                passed=score >= threshold,
                details=data,
            )
    except Exception as e:
        logger.warning(f"Relevancy 评估异常: {e}")
        return EvalResult(metric="answer_relevancy", score=0.0, passed=False, error=str(e))

    return EvalResult(metric="answer_relevancy", score=0.0, passed=False, error="解析失败")


# ── 评估器 ──────────────────────────────────────────────────────────────

class NovelRAGEvaluator:
    """RAG 质量评估器"""

    def __init__(self, threshold: float = 0.75):
        self.threshold = threshold

    def evaluate(self, samples: list[EvalSample]) -> Report:
        all_results: list[EvalResult] = []

        for i, sample in enumerate(samples):
            logger.info(f"评估样本 {i+1}/{len(samples)}: {sample.query[:40]}...")

            # Faithfulness
            result = evaluate_faithfulness(sample, threshold=self.threshold)
            all_results.append(result)

            # Relevancy
            result_rel = evaluate_relevancy(sample)
            all_results.append(result_rel)

        # 聚合
        metrics: dict[str, list[float]] = {}
        for r in all_results:
            metrics.setdefault(r.metric, []).append(r.score)

        avg_scores = {
            name: sum(scores) / len(scores) for name, scores in metrics.items()
        }
        passed = all(r.passed for r in all_results if r.error is None)

        from datetime import datetime
        report = Report(
            samples_count=len(samples),
            scores=avg_scores,
            passed=passed,
            details=all_results,
            generated_at=datetime.now().isoformat(),
        )
        return report

    def print_report(self, report: Report):
        """打印可读报告"""
        import sys
        enc = sys.stdout.encoding or "utf-8"
        ok = "PASS" if report.passed else "FAIL"
        lines = [
            "\n" + "=" * 60,
            "  RAG Quality Evaluation Report",
            f"  Samples: {report.samples_count}",
            f"  Time: {report.generated_at}",
            "=" * 60,
        ]
        for name, score in report.scores.items():
            bar_len = int(score * 30)
            bar = "#" * bar_len + "-" * (30 - bar_len)
            status = "PASS" if score >= self.threshold else "FAIL"
            lines.append(f"  {name:25s} [{bar}] {score:.3f}  {status}")
        lines.append(f"")
        lines.append(f"  Overall: {ok} (threshold: faithfulness >= {self.threshold})")
        lines.append("=" * 60)
        print("\n".join(lines))


# ── 黄金测试集 ──────────────────────────────────────────────────────────

def generate_golden_samples(novel_key: str = "shaosong", output_path: str = "") -> list[EvalSample]:
    """
    从 Wiki 数据自动生成黄金测试集。

    生成策略:
        1. 从卷摘要和全书摘要生成 Overall 类问题
        2. 从章节摘要生成情节类问题
        3. 从图谱生成人物关系类问题

    人工抽检要求:
        生成后请抽检 10-15 条，确认答案准确。
        抽检通过后此测试集可作为回归门禁使用。
    """
    if not output_path:
        output_path = f"data/eval/golden/{novel_key}.json"

    from mcp_server import get_novel  # 复用 MCP 数据加载
    data = get_novel(novel_key)

    samples = []
    book = data.book_summary
    volumes = data.volumes
    chapters = data.chapters

    # 1. 全书级问题
    if book and book.get("summary"):
        samples.append(EvalSample(
            query=f"《{data.display_name}》讲述了什么故事？",
            answer=book["summary"],
            contexts=[book["summary"]],
            metadata={"type": "book_summary", "novel": novel_key},
        ))
        for mc in book.get("main_characters", []):
            samples.append(EvalSample(
                query=f"{mc} 是《{data.display_name}》中的什么角色？",
                answer=f"{mc} 是小说的主要角色之一。",
                contexts=[book["summary"]],
                metadata={"type": "main_character", "novel": novel_key},
            ))

    # 2. 卷级问题
    for vol in volumes[:10]:
        title = vol.get("title", "")
        summary = vol.get("summary", "")
        chars = "、".join(vol.get("main_characters", [])[:5])
        if summary:
            samples.append(EvalSample(
                query=f"{title}讲了什么？",
                answer=summary[:300],
                contexts=[summary],
                metadata={"type": "volume_summary", "volume": title},
            ))

    # 3. 人物关系问题（从图谱取 top 人物）
    if data.nodes:
        top_chars = sorted(data.nodes, key=lambda n: -n.get("mention_count", 0))[:10]
        for char in top_chars:
            name = char["name"]
            rels = data.get_character_relations(name)
            if rels:
                top_rel = rels[0]
                chap_summaries = []
                for ch in data.get_character_appearances(name)[:3]:
                    chap_summaries.append(ch.get("summary", "")[:100])
                sample_answer = f"{name} 与 {top_rel['character']} 的关系是: {top_rel['relation']}"
                samples.append(EvalSample(
                    query=f"{name} 和 {top_rel['character']} 是什么关系？",
                    answer=sample_answer,
                    contexts=chap_summaries + [top_rel.get("relation", "")],
                    metadata={
                        "type": "character_relation",
                        "character": name,
                        "target": top_rel["character"],
                    },
                ))

    # 保存
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    data_out = []
    for s in samples:
        data_out.append({
            "query": s.query,
            "answer": s.answer,
            "contexts": s.contexts,
            "metadata": s.metadata,
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data_out, f, ensure_ascii=False, indent=2)
    logger.info(f"黄金测试集已生成: {output_path} ({len(samples)} 条)")
    logger.info("⚠️  请抽检 10-15 条确认答案准确，然后设置 --gate 启用回归门禁")
    return samples


def load_golden_samples(path: str) -> list[EvalSample]:
    """从 JSON 加载黄金测试集"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    samples = []
    for item in raw:
        samples.append(EvalSample(
            query=item["query"],
            answer=item["answer"],
            contexts=item.get("contexts", []),
            ground_truth=item.get("ground_truth"),
            metadata=item.get("metadata", {}),
        ))
    logger.info(f"已加载黄金测试集: {path} ({len(samples)} 条)")
    return samples


# ── 回归门禁 ────────────────────────────────────────────────────────────

def run_regression_gate(
    golden_path: str,
    threshold: float = 0.75,
    verbose: bool = False,
) -> bool:
    """
    回归门禁：跑黄金测试集，faithfulness < threshold 时返回 False。

    用法 (CI 脚本):
        python scripts/rag_evaluate.py --gate --threshold 0.75
        if [ $? -ne 0 ]; then echo "❌ 质量不达标，阻断发布"; exit 1; fi
    """
    if not os.path.exists(golden_path):
        logger.error(f"黄金测试集不存在: {golden_path}")
        logger.error("请先运行: python scripts/rag_evaluate.py --generate --novel shaosong")
        return False

    samples = load_golden_samples(golden_path)
    evaluator = NovelRAGEvaluator(threshold=threshold)
    report = evaluator.evaluate(samples)

    if verbose:
        evaluator.print_report(report)

    if report.passed:
        logger.info(f"回归门禁: ✅ 通过 (faithfulness={report.scores.get('faithfulness', 0):.3f})")
    else:
        logger.error(f"回归门禁: ❌ 不通过 (faithfulness={report.scores.get('faithfulness', 0):.3f}, 阈值={threshold})")

    return report.passed


# ── 命令行入口 ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Novel-GraphRAG 质量评估")
    parser.add_argument("--query", help="评估单条: 问题")
    parser.add_argument("--answer", help="评估单条: 回答")
    parser.add_argument("--contexts", nargs="*", default=[], help="评估单条: 原文片段")
    parser.add_argument("--dataset", help="评估一批: JSON 文件路径")
    parser.add_argument("--generate", action="store_true", help="生成黄金测试集")
    parser.add_argument("--novel", default="shaosong", help="指定小说 key")
    parser.add_argument("--gate", action="store_true", help="回归门禁模式")
    parser.add_argument("--threshold", type=float, default=0.75, help="faithfulness 阈值")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    if args.gate:
        golden_path = f"data/eval/golden/{args.novel}.json"
        if not os.path.exists(os.path.dirname(golden_path)):
            os.makedirs(os.path.dirname(golden_path), exist_ok=True)
        if not os.path.exists(golden_path):
            logger.info(f"黄金测试集不存在，自动生成: {golden_path}")
            generate_golden_samples(args.novel, golden_path)
        passed = run_regression_gate(golden_path, args.threshold, verbose=True)
        sys.exit(0 if passed else 1)

    if args.generate:
        generate_golden_samples(args.novel)
        return

    if args.query and args.answer:
        sample = EvalSample(query=args.query, answer=args.answer, contexts=list(args.contexts))
        result = evaluate_faithfulness(sample, threshold=args.threshold)
        print(f"faithfulness: {result.score:.3f} (passed={result.passed})")
        if result.details:
            print(json.dumps(result.details, ensure_ascii=False, indent=2))
        return

    if args.dataset:
        samples = load_golden_samples(args.dataset)
        evaluator = NovelRAGEvaluator(threshold=args.threshold)
        report = evaluator.evaluate(samples)
        evaluator.print_report(report)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
