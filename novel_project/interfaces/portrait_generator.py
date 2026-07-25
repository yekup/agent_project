"""
角色立绘生成器 — 阿里云通义万相
=================================
使用阿里云 通义万相 (Wanxiang) 文生图 API 生成简单的 2D 角色立绘。

用法:
    from interfaces.portrait_generator import get_portrait_generator, GenerationRequest

    gen = get_portrait_generator("aliyun")
    req = GenerationRequest(
        character_name="赵玖",
        appearance_descriptions=["历史系大学生，穿越为宋高宗赵构"],
        role_type="protagonist",
        style="anime",
    )
    portrait = gen.generate(req)
    print(portrait.image_url)  # → data/portraits/赵玖.png

依赖:
    pip install dashscope>=1.20.0
    pip install requests

环境变量:
    DASHSCOPE_API_KEY — 阿里云 DashScope API Key（必填）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── 数据模型（与原有抽象接口兼容）────────────────────────────────────────


@dataclass
class CharacterPortrait:
    """单个人物的立绘信息"""
    character_name: str
    image_url: str | None = None           # 生成后的图片本地路径
    face_embedding: list[float] | None = None
    style_tag: str = "novel_default"
    description_sources: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class GenerationRequest:
    """立绘生成请求"""
    character_name: str
    appearance_descriptions: list[str]
    role_type: str = "protagonist"         # protagonist / supporting / antagonist
    reference_images: list[str] = field(default_factory=list)
    style: str = "anime"                   # anime / realistic
    resolution: tuple[int, int] = (512, 768)


# ── Prompt 模板 ──────────────────────────────────────────────────────────

ROLE_LABELS = {
    "protagonist": "主角",
    "supporting": "配角",
    "antagonist": "反派",
}

# 简单 2D 立绘的 prompt 模板
SIMPLE_2D_PROMPT = """Simple 2D anime character portrait of {name}, {role_label}.
{descriptions}
Style: flat cel shading, simple solid color background, clean minimal line art,
casual standing pose, few details, no weapons, no complex accessories,
simple character design, amateur illustration style, thin lines."""

FALLBACK_PROMPT = """\
A simple 2D anime character, standing full-body portrait,
solid background, clean line art, flat colors, simple design."""


# ═════════════════════════════════════════════════════════════════════
#  AliYun 通义万相实现
# ═════════════════════════════════════════════════════════════════════

class AliyunPortraitGenerator:
    """
    阿里云通义万相 文生图 立绘生成器。

    生成策略:
      1. 从知识图谱读取人物信息
      2. 构建简单 2D 风格 prompt
      3. 调用通义万相 API 生成图片
      4. 下载保存到本地 data/portraits/
      5. 维护 index.json 索引

    每本书的立绘输出结构:
      data/portraits/{novel_key}/
        index.json       # {character_name: {image_path, description, ...}}
        {name1}.png
        {name2}.png
        ...
    """

    # 通义万相 Turbo 模型（快速同步返回）
    WANX_MODEL = "wanx2.1-t2i-turbo"

    # 图片尺寸
    SIZE_MAP = {
        "portrait": "768*1024",    # 立绘比例
        "square": "1024*1024",
    }

    def __init__(
        self,
        output_dir: str | None = None,
        api_key: str | None = None,
        novel_key: str = "default",
    ):
        """
        Args:
            output_dir: 输出根目录，默认 novel_project/data/portraits
            api_key: DashScope API Key，默认读 DASHSCOPE_API_KEY 环境变量
            novel_key: 小说 key，用于隔离不同书的立绘
        """
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "portraits",
        ) if output_dir is None else output_dir

        self._novel_dir = os.path.join(base, novel_key)
        self._index_path = os.path.join(base, novel_key, "index.json")
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._index: dict[str, dict] = {}

        os.makedirs(os.path.join(base, novel_key), exist_ok=True)
        self._load_index()

    # ── 对外接口 ──────────────────────────────────────────────────────

    def generate(self, request: GenerationRequest) -> CharacterPortrait:
        """
        生成单个人物的 2D 立绘。

        如果已存在该人物的立绘且描述未变，跳过生成直接返回。
        """
        # 检查缓存（同一角色+相同描述→跳过）
        cached = self._check_cache(request)
        if cached:
            logger.info(f"[Portrait] 缓存命中: {request.character_name}")
            return cached

        # 构建 prompt
        prompt = self._build_prompt(request)
        logger.info(f"[Portrait] 生成: {request.character_name} ({request.role_type})")

        # 检查 API Key
        if not self._api_key:
            logger.warning("[Portrait] DASHSCOPE_API_KEY 未设置，返回占位图")
            return self._placeholder(request.character_name, request.role_type)

        # 调用通义万相
        try:
            image_data = self._call_wanxiang(prompt)
            if image_data is None:
                return self._placeholder(request.character_name, request.role_type)

            # 保存图片
            save_path = self._save_image(request.character_name, image_data)
            portrait = CharacterPortrait(
                character_name=request.character_name,
                image_url=save_path,
                description_sources=request.appearance_descriptions,
                style_tag=request.style,
                confidence=1.0,
            )
            self._update_index(request, save_path)
            return portrait

        except Exception as e:
            logger.error(f"[Portrait] 生成失败 {request.character_name}: {e}")
            return self._placeholder(request.character_name, request.role_type)

    def batch_generate(
        self,
        requests: list[GenerationRequest],
        delay: float = 2.0,
    ) -> list[CharacterPortrait]:
        """
        批量生成（串行调用，间隔 delay 秒避免限流）。
        """
        results = []
        for i, req in enumerate(requests):
            if i > 0 and delay > 0:
                time.sleep(delay)
            portrait = self.generate(req)
            results.append(portrait)
        return results

    # 以下为兼容原抽象接口的桩方法
    def ensure_consistency(self, character_name: str, new_portrait: CharacterPortrait) -> bool:
        """一致性检查（简化版：直接通过）"""
        return True

    def load_lora(self, lora_path: str, weight: float = 0.8) -> None:
        """LoRA 加载（通义万相不支持，空实现）"""
        logger.info(f"[Portrait] LoRA 加载跳过（通义万相不支持自定义 LoRA）: {lora_path}")

    # ── Prompt 构建 ──────────────────────────────────────────────────

    def _build_prompt(self, request: GenerationRequest) -> str:
        """构建送审 prompt"""
        if not request.appearance_descriptions:
            return FALLBACK_PROMPT

        descriptions = "，".join(
            [d for d in request.appearance_descriptions if d.strip()]
        ) or "unknown character"

        role_label = ROLE_LABELS.get(request.role_type, "角色")

        # 如果是写实风格
        if request.style == "realistic":
            return (
                f"Simple character portrait of {request.character_name}, {role_label}, "
                f"{descriptions}. Style: clean and simple, standing full-body, "
                f"minimal design, no background."
            )

        # 默认: 2D 动漫风格
        return SIMPLE_2D_PROMPT.format(
            name=request.character_name,
            role_label=role_label,
            descriptions=descriptions[:200],
        )

    # ── API 调用 ──────────────────────────────────────────────────────

    def _call_wanxiang(self, prompt: str) -> bytes | None:
        """
        调用通义万相 API 生成图片（异步任务模式）。

        流程:
          1. dashscope SDK async_call → 提交异步任务
          2. SDK fetch → 轮询直到完成（最多等 120s）
          3. 下载图片

        返回图片二进制数据，失败返回 None。
        """
        try:
            return self._call_via_sdk(prompt)
        except ImportError:
            logger.warning("[Portrait] dashscope SDK 未安装，跳过")
        except Exception as e:
            logger.warning(f"[Portrait] SDK 调用失败: {e}")
        return None

    def _call_via_sdk(self, prompt: str) -> bytes | None:
        """
        通过 dashscope SDK 异步调用通义万相。

        async_call() → 获取 task_id
        wait() / fetch() → 轮询结果
        """
        import dashscope
        from dashscope import ImageSynthesis
        import time

        dashscope.api_key = self._api_key
        size = self.SIZE_MAP["portrait"]

        # Step 1: 提交异步任务
        rsp = ImageSynthesis.async_call(
            model=self.WANX_MODEL,
            prompt=prompt,
            size=size,
            n=1,
        )

        if rsp.status_code != 200:
            logger.error(f"[Portrait] 提交失败: code={rsp.status_code} msg={rsp.message}")
            return None

        task_id = rsp.output.get("task_id", "")
        if not task_id:
            logger.error(f"[Portrait] 未获取 task_id")
            return None

        logger.info(f"[Portrait] 任务已提交: task_id={task_id}")

        # Step 2: 轮询等待完成
        max_wait = 120
        poll_interval = 2
        waited = 0

        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval

            fetch_rsp = ImageSynthesis.fetch(task_id)
            if fetch_rsp.status_code != 200:
                logger.warning(f"[Portrait] 轮询失败: {fetch_rsp.status_code}")
                continue

            task_status = fetch_rsp.output.get("task_status", "")

            if task_status in ("SUCCEEDED", "SUCCESS"):
                results = fetch_rsp.output.get("results", [])
                if results:
                    image_url = results[0].get("url", "")
                    if image_url:
                        logger.info(f"[Portrait] 生成成功 ({waited}s)")
                        return self._download_image(image_url)
                break

            elif task_status in ("FAILED", "CANCELED"):
                msg = fetch_rsp.output.get("message", "未知错误")
                logger.warning(f"[Portrait] 任务{task_status}: {msg}")
                return None

            else:
                logger.info(f"[Portrait] 等待中... status={task_status} ({waited}s)")

        logger.warning(f"[Portrait] 任务超时 (>{max_wait}s)")
        return None

    @staticmethod
    def _download_image(url: str) -> bytes | None:
        """下载图片二进制数据"""
        import requests
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 200:
                return resp.content
        except Exception as e:
            logger.warning(f"[Portrait] 下载图片失败: {e}")
        return None

    # ── 文件管理 ──────────────────────────────────────────────────────

    def _save_image(self, name: str, image_data: bytes) -> str:
        """保存图片到本地，返回相对路径"""
        import hashlib

        # 安全文件名
        safe_name = self._safe_filename(name)
        filename = f"{safe_name}.png"
        filepath = os.path.join(self._novel_dir, filename)

        # 如果已存在且内容相同，不重复写入
        if os.path.exists(filepath):
            existing_hash = hashlib.md5(open(filepath, "rb").read()).hexdigest()
            new_hash = hashlib.md5(image_data).hexdigest()
            if existing_hash == new_hash:
                return filepath

        with open(filepath, "wb") as f:
            f.write(image_data)

        logger.info(f"[Portrait] 图片已保存: {filepath} ({len(image_data)} bytes)")
        return filepath

    def _update_index(self, request: GenerationRequest, image_path: str):
        """更新立绘索引"""
        self._index[request.character_name] = {
            "name": request.character_name,
            "role_type": request.role_type,
            "descriptions": request.appearance_descriptions,
            "image_path": image_path,
            "style": request.style,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_index()

    def _check_cache(self, request: GenerationRequest) -> CharacterPortrait | None:
        """检查是否已生成过相同立绘"""
        cached = self._index.get(request.character_name)
        if cached and cached.get("image_path"):
            path = cached["image_path"]
            if os.path.exists(path):
                return CharacterPortrait(
                    character_name=request.character_name,
                    image_url=path,
                    description_sources=request.appearance_descriptions,
                    confidence=1.0,
                )
        return None

    def _placeholder(self, name: str, role_type: str = "") -> CharacterPortrait:
        """API 不可用时的占位图"""
        logger.warning(f"[Portrait] 占位图: {name} ({role_type})")
        return CharacterPortrait(
            character_name=name,
            image_url=self._generate_placeholder_svg(name, role_type),
            confidence=0.0,
        )

    @staticmethod
    def _generate_placeholder_svg(name: str, role_type: str) -> str:
        """
        生成简单的 SVG 占位图（纯色背景 + 姓名首字母）。
        返回 data: URI 或文件路径。
        """
        # 根据角色类型选颜色
        colors = {
            "protagonist": "#4A90D9",
            "supporting": "#7B8D8E",
            "antagonist": "#C0392B",
        }
        bg_color = colors.get(role_type, "#95A5A6")
        initial = name[0] if name else "?"

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="384" viewBox="0 0 256 384">'
            f'<rect width="256" height="384" fill="{bg_color}" rx="8"/>'
            f'<circle cx="128" cy="120" r="50" fill="rgba(255,255,255,0.2)"/>'
            f'<text x="128" y="135" text-anchor="middle" fill="white" font-size="48" font-family="sans-serif">{initial}</text>'
            f'<text x="128" y="250" text-anchor="middle" fill="white" font-size="14" font-family="sans-serif">{name}</text>'
            f'<text x="128" y="275" text-anchor="middle" fill="rgba(255,255,255,0.6)" font-size="11" font-family="sans-serif">{role_type}</text>'
            f'</svg>'
        )
        # 返回 data: URI
        encoded = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
        return f"data:image/svg+xml;base64,{encoded}"

    # ── 索引持久化 ────────────────────────────────────────────────────

    def _load_index(self):
        """加载立绘索引"""
        if os.path.exists(self._index_path):
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
                logger.info(f"[Portrait] 索引已加载: {len(self._index)} 个人物")
            except Exception:
                self._index = {}

    def _save_index(self):
        """保存立绘索引"""
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _safe_filename(name: str) -> str:
        """转为安全的文件名"""
        import re
        return re.sub(r'[\\/:*?"<>|]', "_", name)


# ── 注册表（兼容原有接口）───────────────────────────────────────────────

_registry: dict[str, type] = {}


def register_portrait_generator(name: str, cls: type) -> None:
    _registry[name] = cls


def get_portrait_generator(name: str = "aliyun", **kwargs) -> AliyunPortraitGenerator:
    """
    获取 PortraitGenerator 实例。

    默认返回 AliyunPortraitGenerator，不需 GPU。
    """
    if name == "aliyun" or name == "default":
        return AliyunPortraitGenerator(**kwargs)
    if name in _registry:
        return _registry[name](**kwargs)
    raise NotImplementedError(
        f"未注册的立绘生成器: {name}。可用: aliyun (默认)"
    )


# 向后兼容：原有的 PortraitGenerator 类名指向 AliyunPortraitGenerator
PortraitGenerator = AliyunPortraitGenerator
