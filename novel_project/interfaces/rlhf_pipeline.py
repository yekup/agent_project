"""
RLHF 微调闭环接口
==================
收集用户纠错反馈 → 构建偏好数据集 → 微调模型 → 部署新模型。

所需资源:
    - GPU (≥ 24GB VRAM, 推荐 A100 40G 或 2×RTX 3090)
    - 训练框架: TRL / LLaMA-Factory / Axolotl
    - 推理部署: vLLM / TGI

当前状态:
    数据收集与存储层已就绪，训练与部署层待 GPU 环境实现。

架构::

    用户纠错 ──→ FeedbackCollector ──→ PreferenceDataset ──→ FineTuner ──→ ModelDeployer
                      ↑                      ↑                    ↑              ↑
                  数据库存储            版本化数据集           GPU 训练       vLLM 推理
                  ✅ 可做               ✅ 可做               ❌ 需 GPU     ❌ 需 GPU
"""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRecord:
    """单条用户纠错记录"""
    id: str
    novel_name: str
    query: str                     # 用户的原始问题
    original_answer: str           # LLM 原来给出的回答
    corrected_answer: str          # 用户修正后的回答
    category: str = "factual"      # factual / style / completeness / other
    source_chapter: str | None = None  # 涉及哪一章（选填）
    created_at: str = ""
    user_id: str = ""
    confidence: float = 1.0        # 人工修正置信度，始终为 1.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class PreferencePair:
    """
    偏好对：chosen 是用户偏好的回答，rejected 是原始回答。

    此格式与 TRL/DPO 训练框架的输入格式一一对应，
    未来实现微调时可以做零转换直接喂入。
    """
    prompt: str                    # 用户问题 / 系统 prompt + 检索上下文
    chosen: str                    # 用户修正后的回答 (preferred)
    rejected: str                  # LLM 原始回答 (dispreferred)
    metadata: dict = field(default_factory=dict)


@dataclass
class TrainingRun:
    """一次微调训练的元信息"""
    run_id: str
    base_model: str                # 基座模型名，如 "deepseek-chat"
    dataset_size: int              # 偏好对数量
    train_loss: float | None = None
    eval_metric: dict = field(default_factory=dict)
    artifact_path: str | None = None  # 微调后权重路径
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------

class FeedbackCollector(abc.ABC):
    """
    纠错反馈收集器。

    ✅ 可做部分：完整实现。
    将用户前端提交的修正数据持久化，支持版本回滚。
    """

    @abc.abstractmethod
    def save_feedback(self, record: FeedbackRecord) -> str:
        """
        存储一条用户纠错。

        实现要求:
            - 写入 data/feedback/{novel_name}/{record.id}.json
            - 同时记录到版本化存储（支持回滚）
        """
        ...

    @abc.abstractmethod
    def get_feedback(self, feedback_id: str) -> FeedbackRecord | None:
        ...

    @abc.abstractmethod
    def list_feedback(
        self, novel_name: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackRecord]:
        ...

    @abc.abstractmethod
    def rollback(self, feedback_id: str) -> None:
        """
        回滚某条修正（将 wiki 恢复到此条修正前的状态）。
        需要版本化存储支持。
        """
        ...


class PreferenceDatasetBuilder(abc.ABC):
    """
    偏好数据集构建器。

    ✅ 可做部分：完整实现。
    将原始纠错记录转化为训练框架可消费的 PreferencePair 格式。
    """

    @abc.abstractmethod
    def build_pairs(
        self,
        feedback_records: list[FeedbackRecord],
        include_prompt_context: bool = True,
    ) -> list[PreferencePair]:
        """
        将纠错记录转换为偏好对。

        转换逻辑:
            1. prompt = 用户原始 query (system prompt 由 FineTuner 拼接)
            2. chosen = corrected_answer
            3. rejected = original_answer

        实现要求:
            - 自动去重同一 query 的多次修正（保留最新）
            - 过滤掉 confidence < 0.5 的记录
        """
        ...

    @abc.abstractmethod
    def export_to_jsonl(self, pairs: list[PreferencePair], output_path: str) -> str:
        """
        导出为 JSONL 格式（TRL/LLaMA-Factory 标准输入格式）。

        每行: {"prompt": "...", "chosen": "...", "rejected": "..."}
        """
        ...

    @abc.abstractmethod
    def dataset_stats(self, pairs: list[PreferencePair]) -> dict:
        """
        统计数据集基本信息:
            - 总条数
            - 各类别分布
            - 平均 prompt/chosen/rejected 长度
            - 数据时间范围
        """
        ...


class FineTuner(abc.ABC):
    """
    模型微调器。

    ❌ 需 GPU，仅定义接口。
    """

    RUNS_DIR = Path("data/training_runs")

    @abc.abstractmethod
    async def run_dpo(
        self,
        base_model: str,
        dataset: list[PreferencePair],
        hyperparams: dict | None = None,
    ) -> TrainingRun:
        """
        执行 DPO (Direct Preference Optimization) 微调。

        参数:
            base_model: 基座模型名称
            dataset: 偏好数据集
            hyperparams: {
                "learning_rate": 5e-6,
                "num_train_epochs": 3,
                "per_device_batch_size": 4,
                "gradient_accumulation_steps": 8,
                "beta": 0.1,            # DPO 温度参数
            }

        实现依赖:
            - TRL (Transformer Reinforcement Learning) 库
            - GPU: 至少 24GB VRAM（7B 模型 LoRA 微调）

        参考命令:
            ```bash
            pip install trl peft accelerate bitsandbytes
            accelerate launch scripts/train_dpo.py \\
                --model_name deepseek-coder-7b \\
                --dataset_path data/training/dpo_dataset.jsonl
            ```
        """
        ...

    @abc.abstractmethod
    async def run_sft(
        self,
        base_model: str,
        dataset: list[PreferencePair],
        hyperparams: dict | None = None,
    ) -> TrainingRun:
        """
        执行 SFT (Supervised Fine-Tuning) 微调。
        只使用 chosen 回答，适用于首次微调冷启动。
        """
        ...

    @abc.abstractmethod
    def list_runs(self) -> list[TrainingRun]:
        """列出所有历史训练记录"""
        ...

    @abc.abstractmethod
    def evaluate(self, run: TrainingRun, test_dataset: list[PreferencePair]) -> dict:
        """
        在测试集上评估微调效果。

        返回指标:
            - eval_loss
            - accuracy (chosen 是否比 rejected 得分高)
            - reward 分数
        """
        ...


class ModelDeployer(abc.ABC):
    """
    模型部署器。

    ❌ 需 GPU，仅定义接口。

    预期部署流程:
        FineTuner 产出权重 → ModelDeployer 加载 → 暴露与现有 call_llm() 兼容的接口
    """

    @abc.abstractmethod
    async def deploy(self, run: TrainingRun, port: int = 8001) -> str:
        """
        部署微调后的模型。

        返回: API 端点 URL，如 "http://localhost:8001/v1/chat/completions"

        实现依赖:
            - vLLM (推荐) 或 Text Generation Inference (TGI)
            ```bash
            python -m vllm.entrypoints.openai.api_server \\
                --model runs/novel-dpo-v3 \\
                --port 8001
            ```
        """
        ...

    @abc.abstractmethod
    def rollback(self, version: str) -> bool:
        """
        回滚到上一个稳定版本。
        """
        ...

    @abc.abstractmethod
    def health_check(self, endpoint: str) -> dict:
        """
        检查部署状态。
        """
        ...


# ---------------------------------------------------------------------------
# 完整管道编排 (仅接口定义)
# ---------------------------------------------------------------------------

class RLHFPipeline(abc.ABC):
    """
    RLHF 全流程编排。

    调用方只需:
        pipeline = RLHFPipeline(collector, builder, tuner, deployer)
        run = await pipeline.run_full_cycle(novel_name="斗破苍穹")

    整个过程自动完成: 收集数据 → 构建数据集 → 微调 → 部署 → 验证。
    当 tuner 或 deployer 未实现时（GPU 不可用），抛出 NotImplementedError
    并给出环境配置指引。
    """

    collector: FeedbackCollector
    dataset_builder: PreferenceDatasetBuilder
    fine_tuner: FineTuner
    deployer: ModelDeployer

    def __init__(
        self,
        collector: FeedbackCollector,
        dataset_builder: PreferenceDatasetBuilder,
        fine_tuner: FineTuner | None = None,
        deployer: ModelDeployer | None = None,
    ):
        self.collector = collector
        self.dataset_builder = dataset_builder
        self.fine_tuner = fine_tuner
        self.deployer = deployer

    async def run_full_cycle(
        self,
        novel_name: str,
        min_feedback: int = 50,
        base_model: str = "deepseek-chat",
    ) -> TrainingRun | None:
        """
        执行完整 RLHF 周期。

        步骤:
            1. 收集 novel_name 下所有纠错反馈
            2. 若条数 < min_feedback，返回 None 提示数据不足
            3. 构建偏好数据集
            4. 微调（需 GPU）
            5. 部署（需 GPU）
            6. 返回 TrainingRun 元信息

        当无 GPU 环境时:
            raise NotImplementedError("...")
        """
        records = self.collector.list_feedback(novel_name=novel_name)
        if len(records) < min_feedback:
            logger.info(
                f"[RLHF] {novel_name}: 仅 {len(records)} 条反馈，"
                f"需 ≥ {min_feedback} 条才能启动微调"
            )
            return None

        if self.fine_tuner is None or self.deployer is None:
            raise NotImplementedError(
                "RLHF 全流程需要 GPU 环境。\n"
                "1. 安装 CUDA + PyTorch\n"
                "2. pip install trl peft accelerate bitsandbytes\n"
                "3. pip install vllm  # 用于部署\n"
                "参考数据: 50 条偏好对 ≈ 30 分钟 LoRA 微调 (A100)"
            )

        pairs = self.dataset_builder.build_pairs(records)
        run = await self.fine_tuner.run_dpo(base_model, pairs)
        endpoint = await self.deployer.deploy(run)
        logger.info(f"[RLHF] 部署完成: {endpoint}")
        return run
