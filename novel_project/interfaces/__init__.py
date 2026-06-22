"""
Novel-GraphRAG 接口层
====================
为需要 GPU / 外部资源 / 商业合作 才能实现的功能预留抽象接口，
确保未来填补实现时不需要修改调用方代码。

使用方式：
    # 在项目初始化时注册具体实现
    from interfaces.portrait_generator import PortraitGenerator
    PortraitGenerator.register("sd_impl", SDPortraitGenerator)

    # 或者直接实例化（有实现时）
    generator = PortraitGenerator()  # 无实现时抛出 NotImplementedError
"""

from interfaces.portrait_generator import PortraitGenerator
from interfaces.rlhf_pipeline import RLHFPipeline
from interfaces.llm_provider import LLMProvider, DeepSeekProvider, LocalModelProvider
from interfaces.copyright_verifier import CopyrightVerifier
from interfaces.graph_storage import GraphStorageBackend, Neo4jBackend, ColdStorageBackend
from interfaces.eval_metrics import NovelEvalMetric, FaithfulnessMetric, PowerLevelConsistencyMetric

__all__ = [
    "PortraitGenerator",
    "RLHFPipeline",
    "LLMProvider", "DeepSeekProvider", "LocalModelProvider",
    "CopyrightVerifier",
    "GraphStorageBackend", "Neo4jBackend", "ColdStorageBackend",
    "NovelEvalMetric", "FaithfulnessMetric", "PowerLevelConsistencyMetric",
]
