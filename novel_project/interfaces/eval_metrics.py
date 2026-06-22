"""
可扩展评估指标接口
==================
用于 RAG 质量评估体系（v2.0 §3.1）中的插件化指标。

设计:
    FaithfulnessMetric        → ✅ 可实现，基于 LLM-as-Judge
    PowerLevelConsistencyMetric → 🟠 不建议实现 (见下方说明)

关于"战力一致性"指标 (v2.1 §1.2):
    文档提出在玄幻类中增加 power_level_consistency 指标，
    但这是一个**开放研究问题**而非工程问题:
        - "战力一致"的定义具有主观性 —— 主角越级打怪是爽点还是 bug？
        - 缺乏 ground truth —— 同一场战斗不同读者解读不同
        - 玄幻小说的"实力体系"本身往往是模糊的，作者自己都可能吃书
    建议直接用 FaithfulnessMetric 覆盖:
        如果报告断言"萧炎以大斗师境界击败了斗王"，faithfulness 检查
        会抓到这个断言缺乏原文依据，效果等同于战力一致性检测。

    因此 PowerLevelConsistencyMetric 作为接口保留但不推荐实现，
        未来如果你有明确的评判标准可以再补充。
"""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class EvalSample:
    """单条评估样本"""
    query: str
    answer: str                       # LLM 生成的回答
    contexts: list[str]               # 检索到的上下文 (Wiki / 原文)
    ground_truth: str | None = None   # 标准答案（黄金测试集用）
    metadata: dict = field(default_factory=dict)


@dataclass
class MetricResult:
    """单条评估结果"""
    metric_name: str
    score: float                      # 0.0 ~ 1.0
    passed: bool                      # score >= threshold
    details: dict | None = None       # 详细诊断信息
    error: str | None = None


@dataclass
class EvaluationReport:
    """完整评估报告"""
    samples_count: int
    metrics: dict[str, float]         # {metric_name: avg_score}
    details: list[MetricResult]
    passed: bool                      # 所有指标通过
    generated_at: str = ""


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class NovelEvalMetric(abc.ABC):
    """
    评估指标基类。

    实现一个新指标的步骤:
        1. 继承 NovelEvalMetric
        2. 实现 score() 方法
        3. 设置指标阈值
        4. 注册到评估器

    Example:
        class MyMetric(NovelEvalMetric):
            name = "my_metric"
            threshold = 0.7

            def score(self, sample: EvalSample) -> MetricResult:
                ...
    """

    name: str = "base_metric"
    threshold: float = 0.7            # 及格线
    description: str = ""

    @abc.abstractmethod
    def score(self, sample: EvalSample) -> MetricResult:
        """对单条样本打分"""
        ...

    def aggregate(self, results: list[MetricResult]) -> float:
        """聚合多条结果为单一分数，默认取均值"""
        valid = [r.score for r in results if r.error is None]
        return sum(valid) / len(valid) if valid else 0.0


# ---------------------------------------------------------------------------
# 内置指标: Faithfulness (基于 LLM-as-Judge)
# ---------------------------------------------------------------------------

NOVEL_FAITHFULNESS_PROMPT = """你是一个网文分析报告审核员。请判断以下回答中的每条结论是否**有原文依据**。

用户问题: {query}

检索到的原文片段:
{contexts}

待审核回答:
{answer}

请逐句判断。对每条结论:
1. 结论原文引用:
2. 是否能在原文片段中找到直接支持:
3. 是否存在编造或过度推理:

以 JSON 格式返回:
{{
    "total_claims": 5,
    "supported_claims": 4,
    "faithfulness_score": 0.8,
    "unsupported_claims": [
        {{"claim": "具体结论文本", "reason": "为什么认为无依据"}}
    ],
    "overall_verdict": "pass"  // "pass" 或 "fail"
}}
"""


class FaithfulnessMetric(NovelEvalMetric):
    """
    Faithfulness 指标 —— 回答是否忠于原文。

    ✅ 可完整实现。
    使用 LLM-as-Judge 判断每条结论是否有原文直接支持。

    ✅ 作为回归门禁 (v2.0 §3.1):
        Faithfulness < 0.75 阻断发布。
    """

    name = "faithfulness"
    threshold = 0.75
    description = "回答中的每条结论是否有原文直接支持，防止 LLM 编造"

    def __init__(self, llm_judge: Callable | None = None):
        """
        Args:
            llm_judge: 调用 Judge LLM 的函数，默认使用项目现有 call_llm
        """
        self._judge = llm_judge or self._default_judge

    @staticmethod
    def _default_judge(prompt: str) -> str:
        """使用项目现有 LLM 调用"""
        from agent_project.core.llm import call_llm
        return call_llm([{"role": "user", "content": prompt}])

    def score(self, sample: EvalSample) -> MetricResult:
        prompt = NOVEL_FAITHFULNESS_PROMPT.format(
            query=sample.query,
            contexts="\n\n".join(sample.contexts[:5]),
            answer=sample.answer,
        )
        try:
            response = self._judge(prompt)
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                score = data.get("faithfulness_score", 0.0) / 1.0
                return MetricResult(
                    metric_name=self.name,
                    score=score,
                    passed=score >= self.threshold,
                    details=data,
                )
        except Exception as e:
            logger.warning(f"[FaithfulnessMetric] Judge 调用失败: {e}")

        return MetricResult(
            metric_name=self.name,
            score=0.0,
            passed=False,
            error="Judge LLM 调用失败",
        )


# ---------------------------------------------------------------------------
# 预留指标: 战力一致性 (不建议实现)
# ---------------------------------------------------------------------------

class PowerLevelConsistencyMetric(NovelEvalMetric):
    """
    战力一致性指标。

    🟠 不建议实现，原因见模块文档和 eval_metrics.py 头部说明。

    如果你仍然想实现，需要先回答:
        1. "战力一致"的操作化定义是什么？
            - 是否跨章比较修为等级数值？
            - 越级战斗如何判定（是 bug 还是剧情需要）？
        2. 评判标准谁来定？
            - 作者自己可能吃书，以哪章为准？
            - 同一场战斗不同读者的理解不同
        3. Ground truth 如何构建？
            - 需要标注团队逐章标记修为等级

    替代方案:
        用 FaithfulnessMetric 间接覆盖 —— 如果回答中的战力断言
        有原文依据，faithfulness 检查会通过；无依据时会标记。
    """

    name = "power_level_consistency"
    threshold = 0.7
    description = "检查玄幻小说中关于修为/战力的描述是否前后一致"

    def score(self, sample: EvalSample) -> MetricResult:
        raise NotImplementedError(
            f"「{self.description}」指标尚未实现。\n"
            f"原因: 这是一个开放研究问题，而非工程问题。\n"
            f"建议先用 FaithfulnessMetric (faithfulness) 替代:\n"
            f"  - 检查回答中的战力断言是否有原文依据\n"
            f"  - 如果答案直接引用原文，faithfulness 会通过\n"
            f"  - 如果答案编造了不存在的战力信息，faithfulness 会标记\n\n"
            f"如果你有明确的战力评判标准（如逐章修为等级对照表），\n"
            f"可以实现此接口并注册到评估器。"
        )


# ---------------------------------------------------------------------------
# 评估器
# ---------------------------------------------------------------------------

class NovelRAGEvaluator:
    """
    RAG 质量评估器。

    使用示例:
        evaluator = NovelRAGEvaluator(metrics=[
            FaithfulnessMetric(),
        ])
        report = evaluator.evaluate(samples)
        print(report.passed)  # True / False
    """

    def __init__(self, metrics: list[NovelEvalMetric] | None = None):
        self.metrics = metrics or [FaithfulnessMetric()]

    def register_metric(self, metric: NovelEvalMetric) -> None:
        """注册新指标（供未来扩展）"""
        self.metrics.append(metric)
        logger.info(f"[Evaluator] 注册指标: {metric.name}")

    def evaluate(self, samples: list[EvalSample]) -> EvaluationReport:
        """执行全量评估"""
        all_results: list[MetricResult] = []

        for sample in samples:
            for metric in self.metrics:
                try:
                    result = metric.score(sample)
                    all_results.append(result)
                except NotImplementedError as e:
                    logger.warning(f"[Evaluator] 跳过 {metric.name}: {e}")
                except Exception as e:
                    logger.error(f"[Evaluator] {metric.name} 评估异常: {e}")
                    all_results.append(MetricResult(
                        metric_name=metric.name,
                        score=0.0,
                        passed=False,
                        error=str(e),
                    ))

        # 聚合
        agg: dict[str, list[float]] = {}
        for r in all_results:
            agg.setdefault(r.metric_name, []).append(r.score)

        metrics_summary = {
            name: sum(scores) / len(scores)
            for name, scores in agg.items()
        }

        passed = all(r.passed for r in all_results if r.error is None)

        from datetime import datetime
        return EvaluationReport(
            samples_count=len(samples),
            metrics=metrics_summary,
            details=all_results,
            passed=passed,
            generated_at=datetime.now().isoformat(),
        )
