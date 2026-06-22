"""
LLM 提供商抽象层
=================
支持多模型后端的统一调用接口，并预留"云端故障→本地备胎"的降级策略。

设计:
    当前实现:    DeepSeekProvider (通过 call_llm 调用 DeepSeek API)
    待实现:      LocalModelProvider (Qwen-7B / ChatGLM 本地推理)

降级策略:
    LLMRouter 自动检测 Provider 健康状态，不健康时切换到备胎。
    备胎配置在 config.yaml 中:
        ```yaml
        llm:
            primary: deepseek
            fallback: local_qwen
            fallback_strategy: degrade  # degrade = 减少结论+强化原文引用
        ```

所需资源 (LocalModelProvider):
    - GPU (≥ 6GB VRAM 可运行 7B INT4 模型)
    - 模型权重 (Qwen2-7B-Instruct-GPTQ-Int4 ≈ 4GB)
    - transformers + auto-gptq 或 llama.cpp
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    """模型等级（对应 Token 分级路由 v2.0 §3.3）"""
    PRO = "pro"       # 云端高端模型 (DeepSeek V4)
    LIGHT = "light"   # 低成本模型 (DeepSeek Lite / Qwen-72B)
    LOCAL = "local"   # 本地模型 (Qwen-7B-int4)
    SKIP = "skip"     # 跳过，复用摘要


@dataclass
class LLMConfig:
    """Provider 配置"""
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    timeout: float = 60.0
    max_retries: int = 3
    extra_kwargs: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    """统一响应格式"""
    content: str
    model: str
    usage: dict | None = None            # {"prompt_tokens": N, "completion_tokens": N}
    latency_ms: float = 0.0
    provider_name: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class LLMProvider(abc.ABC):
    """
    LLM 提供商抽象基类。

    所有具体 Provider 实现此接口，LLMRouter 通过此接口路由请求。
    """

    config: LLMConfig
    provider_name: str = "base"

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()

    @abc.abstractmethod
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """
        标准对话接口。

        Args:
            messages: [{"role": "user/system/assistant", "content": "..."}]
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            response_format: 如 {"type": "json_object"} 强制 JSON 输出
        """
        ...

    @abc.abstractmethod
    def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Any:
        """
        流式对话接口，返回迭代器。

        Yields:
            str: 每个 chunk 的文本片段
        """
        ...

    @abc.abstractmethod
    def health_check(self) -> dict:
        """
        健康检查。

        Returns:
            {"status": "ok" | "degraded" | "down",
             "latency_ms": float,
             "model": str}
        """
        ...

    @abc.abstractmethod
    def count_tokens(self, text: str) -> int:
        """
        预估 token 数。

        云端 API 可用 tiktoken，本地模型用对应的 tokenizer。
        用于预算管控看板 (v2.0 §3.3.2)。
        """
        ...


# ---------------------------------------------------------------------------
# 当前实现 (DeepSeek API)
# ---------------------------------------------------------------------------

class DeepSeekProvider(LLMProvider):
    """
    当前项目使用的 DeepSeek API Provider。

    ✅ 已实现，直接对接 core/llm.py 中的 call_llm。
    """

    provider_name = "deepseek"

    def __init__(self, config: LLMConfig | None = None):
        super().__init__(config)
        # 复用项目现有 call_llm 函数
        from agent_project.core.llm import call_llm
        self._call_llm = call_llm

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> LLMResponse:
        t0 = time.time()
        try:
            content = self._call_llm(messages, temperature=temperature)
            return LLMResponse(
                content=content,
                model=self.config.model_name or "deepseek-chat",
                latency_ms=(time.time() - t0) * 1000,
                provider_name=self.provider_name,
            )
        except Exception as e:
            return LLMResponse(
                content="",
                model="",
                latency_ms=(time.time() - t0) * 1000,
                provider_name=self.provider_name,
                error=str(e),
            )

    def chat_stream(self, messages, temperature=0.7, max_tokens=2048):
        # DeepSeek API 支持流式，复用现有逻辑
        from agent_project.core.llm import call_llm_stream
        return call_llm_stream(messages, temperature=temperature)

    def health_check(self) -> dict:
        t0 = time.time()
        try:
            resp = self.chat(
                [{"role": "user", "content": "ping"}],
                max_tokens=2,
            )
            return {
                "status": "ok" if not resp.error else "down",
                "latency_ms": (time.time() - t0) * 1000,
                "model": self.config.model_name or "deepseek-chat",
                "error": resp.error,
            }
        except Exception as e:
            return {"status": "down", "latency_ms": -1, "model": "", "error": str(e)}

    def count_tokens(self, text: str) -> int:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))


# ---------------------------------------------------------------------------
# 待实现 (本地模型)
# ---------------------------------------------------------------------------

class LocalModelProvider(LLMProvider):
    """
    本地模型 Provider。

    ❌ 需要 GPU 环境，当前为接口桩代码。

    实现方案 (任选其一):
        A. transformers + auto-gptq (GPU, 推荐)
            ```bash
            pip install transformers auto-gptq optimum
            python -c "from transformers import AutoModelForCausalLM; ..."
            ```
        B. llama.cpp (CPU/GPU 混合, 速度较慢)
            ```bash
            pip install llama-cpp-python
            wget https://huggingface.co/Qwen/Qwen2-7B-Instruct-GGUF/resolve/main/qwen2-7b-instruct-q4_K_M.gguf
            ```
        C. vLLM (GPU, 高吞吐)
            ```bash
            pip install vllm
            python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2-7B-Instruct
            ```

    性能预期 (7B INT4):
        - GPU (RTX 3090): ~40 tokens/s
        - CPU (AVX2):    ~3 tokens/s (不推荐用于交互式场景)
    """

    provider_name = "local_qwen"

    def __init__(self, config: LLMConfig | None = None):
        super().__init__(config)
        self._model = None      # 延迟加载
        self._tokenizer = None

    def _load_model(self):
        """
        延迟加载本地模型。

        TODO: 实现模型加载逻辑，参考:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(
                "Qwen/Qwen2-7B-Instruct-GPTQ-Int4",
                trust_remote_code=True,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                "Qwen/Qwen2-7B-Instruct-GPTQ-Int4",
                device_map="auto",
                trust_remote_code=True,
            )
        """
        raise NotImplementedError(
            "LocalModelProvider 需要 GPU 环境。\n"
            "安装指南:\n"
            "  1. pip install torch --index-url https://download.pytorch.org/whl/cu118\n"
            "  2. pip install transformers auto-gptq optimum\n"
            "  3. 下载模型权重: git lfs clone https://huggingface.co/Qwen/Qwen2-7B-Instruct-GPTQ-Int4\n"
            "  4. 修改 config.yaml: llm.primary = 'local_qwen'"
        )

    def chat(self, messages, temperature=0.7, max_tokens=2048, response_format=None) -> LLMResponse:
        self._load_model()  # 抛出 NotImplementedError
        ...

    def chat_stream(self, messages, temperature=0.7, max_tokens=2048):
        self._load_model()
        ...

    def health_check(self) -> dict:
        return {"status": "not_implemented", "latency_ms": -1, "model": "qwen2-7b-int4"}

    def count_tokens(self, text: str) -> int:
        try:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                "Qwen/Qwen2-7B-Instruct-GPTQ-Int4",
                trust_remote_code=True,
            )
            return len(tokenizer.encode(text))
        except Exception:
            # 退化为字符数估算
            return len(text) * 2


# ---------------------------------------------------------------------------
# 路由 & 降级策略
# ---------------------------------------------------------------------------

class FallbackStrategy(Enum):
    """降级策略"""
    RAISE = "raise"                 # 直接报错
    DEGRADE = "degrade"             # 降级模式 (减少结论数 + 强化原文引用)
    FALLBACK_MODEL = "fallback"     # 切换到备胎模型


class LLMRouter:
    """
    LLM 请求路由器，自动检测 Provider 健康状态并执行降级。

    配置示例 (config.yaml):
        ```yaml
        llm:
            primary:
                provider: deepseek
                config: {api_key: "${DEEPSEEK_API_KEY}"}
            fallback:
                provider: local_qwen
                config: {model_name: "Qwen2-7B-Instruct"}
            strategy: fallback  # raise | degrade | fallback
        ```

    TODO (LocalModelProvider 实现后):
        1. 在 config.yaml 中配置 fallback provider
        2. LLMRouter 自动检测 primary 健康状态
        3. 不健康时无缝切换到 fallback，同时给前端打降级标记
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        strategy: FallbackStrategy = FallbackStrategy.FALLBACK_MODEL,
    ):
        self.primary = primary
        self.fallback = fallback
        self.strategy = strategy

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> LLMResponse:
        # 尝试 primary
        resp = self.primary.chat(messages, temperature, max_tokens, response_format)
        if resp.error is None:
            return resp

        # primary 失败，执行降级
        logger.warning(f"[LLMRouter] Primary 失败: {resp.error}")

        if self.strategy == FallbackStrategy.RAISE:
            raise RuntimeError(f"LLM primary unavailable: {resp.error}")

        if self.strategy == FallbackStrategy.FALLBACK_MODEL:
            if self.fallback is None:
                raise RuntimeError("Fallback provider not configured")
            logger.info("[LLMRouter] 切换到 fallback provider")
            fallback_resp = self.fallback.chat(messages, temperature, max_tokens, response_format)
            return fallback_resp

        if self.strategy == FallbackStrategy.DEGRADE:
            # 降级模式：减少生成量 + 返回降级标记
            degrade_prompt = (
                "【降级模式】请用最简洁的方式回答，"
                "控制在 3 句话以内，仅引用已有资料。\n\n" + messages[-1]["content"]
            )
            resp = self.primary.chat(
                [*messages[:-1], {"role": "user", "content": degrade_prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            resp.extra_kwargs = resp.extra_kwargs or {}
            resp.extra_kwargs["degraded"] = True
            return resp

        return resp

    def health_check(self) -> dict:
        primary_health = self.primary.health_check()
        result = {
            "primary": primary_health,
            "strategy": self.strategy.value,
        }
        if self.fallback:
            result["fallback"] = self.fallback.health_check()
        return result
