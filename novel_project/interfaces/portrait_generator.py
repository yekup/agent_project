"""
角色立绘生成接口 (IP-Adapter / Stable Diffusion)
==================================================
预留接口，待 GPU 环境就绪后实现。

所需资源:
    - GPU (≥ 8GB VRAM)
    - PyTorch + diffusers + controlnet + IP-Adapter
    - 角色 LoRA 模型 (需针对每本书训练或使用通用角色 LoRA)

参考实现:
    - IP-Adapter: https://github.com/tencent-ailab/IP-Adapter
    - InstantID: https://github.com/InstantID/InstantID
    - PhotoMaker: https://github.com/TencentARC/PhotoMaker
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class CharacterPortrait:
    """单个人物的立绘信息"""
    character_name: str
    image_url: str | None = None           # 生成后的图片 URL / 本地路径
    face_embedding: list[float] | None = None  # 人脸特征向量，用于跨章一致性
    style_tag: str = "novel_default"       # 风格标签，对应 LoRA 模型
    description_sources: list[str] = field(default_factory=list)  # 外观描写的原文出处
    confidence: float = 0.0                # 生成置信度


@dataclass
class GenerationRequest:
    """立绘生成请求"""
    character_name: str
    appearance_descriptions: list[str]    # 来自 Wiki 的人物外貌描写
    role_type: str = "protagonist"         # protagonist / supporting / antagonist
    reference_images: list[str] = field(default_factory=list)  # 参考图 URL（选填）
    style: str = "realistic"               # realistic / anime / ink_wash
    resolution: tuple[int, int] = (512, 768)


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class PortraitGenerator(abc.ABC):
    """
    角色立绘生成器。

    接口设计原则:
        1. 调用方只需传入人物名 + 外貌描写，不需要关心底层是 IP-Adapter 还是 InstantID
        2. 保证全书同一个人物的人脸一致性 (face_embedding 校验)
        3. 当 GPU 忙碌或模型加载失败时优雅降级

    TODO (需要 GPU 环境):
        - pip install torch diffusers transformers controlnet-aux
        - 下载 IP-Adapter 模型权重
        - 部署 LoRA 微调管道
    """

    @abc.abstractmethod
    def generate(self, request: GenerationRequest) -> CharacterPortrait:
        """
        根据外貌描写生成角色立绘。

        实现时需注意:
            1. 使用 face_embedding 校验同一人物的一致性
            2. 将 image 上传到对象存储或本地缓存
            3. 记录生成耗时的 metrics
        """
        ...

    @abc.abstractmethod
    def batch_generate(self, requests: list[GenerationRequest]) -> list[CharacterPortrait]:
        """
        批量生成（多人物并行）。

        实现建议:
            - 使用 GPU batch inference 提升吞吐
            - 控制 batch_size 避免 OOM
            - 失败条目单独 retry 而非整体重来
        """
        ...

    @abc.abstractmethod
    def ensure_consistency(self, character_name: str, new_portrait: CharacterPortrait) -> bool:
        """
        检查新生成的人脸与历史版本是否一致。

        返回 False 时调用方应考虑重新生成或降级使用兜底方案。
        一致性判断基于 face_embedding 余弦相似度，建议阈值 ≥ 0.6。
        """
        ...

    @abc.abstractmethod
    def load_lora(self, lora_path: str, weight: float = 0.8) -> None:
        """
        加载风格 LoRA（每本书可有专属风格）。

        Args:
            lora_path: LoRA 权重文件路径 (.safetensors)
            weight: LoRA 混合权重
        """
        ...

    # ------------------------------------------------------------------
    # 降级兜底
    # ------------------------------------------------------------------

    def fallback_placeholder(self, character_name: str, role_type: str) -> CharacterPortrait:
        """
        当 GPU 不可用或生成失败时，返回占位头像。

        默认实现根据角色类型返回不同颜色的占位图。
        前端应检测 image_url 是否是占位图并展示对应 UI。
        """
        logger.warning(f"[PortraitGenerator] 使用占位图: {character_name} ({role_type})")
        return CharacterPortrait(
            character_name=character_name,
            image_url=f"/static/placeholders/{role_type}.svg",
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# 已注册实现查询
# ---------------------------------------------------------------------------

_registry: dict[str, type[PortraitGenerator]] = {}


def register_portrait_generator(name: str, cls: type[PortraitGenerator]) -> None:
    _registry[name] = cls


def get_portrait_generator(name: str = "default") -> PortraitGenerator:
    """
    获取 PortraitGenerator 实例。

    当无实现注册时抛出 NotImplementedError，前端应据此展示"即将上线"提示。

    Usage:
        try:
            gen = get_portrait_generator()
            portrait = gen.generate(request)
        except NotImplementedError:
            # 前端展示功能灰度提示
            pass
    """
    if name in _registry:
        return _registry[name]()
    raise NotImplementedError(
        "角色立绘功能尚未就绪。需要 GPU 环境 + IP-Adapter 或类似模型部署。\n"
        "参考配置: pip install torch diffusers transformers && "
        "下载 IP-Adapter 权重到 models/ip-adapter/"
    )
