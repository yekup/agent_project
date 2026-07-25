"""
LLM 统一调用层
==============
封装 OpenAI SDK 兼容的 API 调用。
支持多 Provider: DeepSeek (默认) / Alibaba 通义千问 / OpenAI。

Provider 选择:
  - 设置环境变量 LLM_PROVIDER=deepseek (默认) | alibaba
  - 或通过 LLM_MODEL / LLM_BASE_URL 完全自定义

API Key:
  - deepseek:  DEEPSEEK_API_KEY（主要）
  - alibaba:   DASHSCOPE_API_KEY（仅用于通义万相生成立绘）
  - 当设的 key 不存在时自动尝试另一个
"""
from __future__ import annotations

import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Provider 注册表 ───────────────────────────────────────────────────────
# 顺序决定自动检测优先级：DeepSeek 优先（用于 LLM 问答）
# Alibaba 仅用于通义万相生成立绘，不走 LLM 层

PROVIDERS = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
        "label": "DeepSeek",
    },
    "alibaba": {
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "label": "Alibaba 通义千问",
    },
}

_DEFAULT_PROVIDER = "deepseek"


def _resolve_provider() -> tuple[str, str, str]:
    """
    解析当前生效的 provider 配置。

    返回 (api_key, base_url, model_name)
    优先级:
      1. LLM_MODEL / LLM_BASE_URL 环境变量 → 完全自定义
      2. LLM_PROVIDER 指定 provider → 查注册表
      3. 自动检测: 哪个 API key 设了用哪个
      4. 默认 → alibaba
    """
    # 用户明确指定了 base_url → 完全自定义模式
    custom_base = os.environ.get("LLM_BASE_URL", "").strip()
    custom_model = os.environ.get("LLM_MODEL", "").strip()

    provider_name = os.environ.get("LLM_PROVIDER", "").strip().lower()

    # 尝试按 LLM_PROVIDER 选择
    if provider_name and provider_name in PROVIDERS:
        p = PROVIDERS[provider_name]
        api_key = os.environ.get(p["api_key_env"], "").strip()
        if api_key:
            return (
                api_key,
                custom_base or p["base_url"],
                custom_model or p["default_model"],
            )

    # LLM_PROVIDER=alibaba 但 DASHSCOPE_API_KEY 没设 → 尝试 deepseek 兜底
    if provider_name:
        fallbacks = [n for n in PROVIDERS if n != provider_name]
    else:
        fallbacks = list(PROVIDERS.keys())

    for name in fallbacks:
        p = PROVIDERS[name]
        api_key = os.environ.get(p["api_key_env"], "").strip()
        if api_key:
            logger.info(f"LLM Provider: {p['label']} (via {p['api_key_env']})")
            return (
                api_key,
                custom_base or p["base_url"],
                custom_model or p["default_model"],
            )

    # 默认走 alibaba（key 可能为空，后面报错）
    p = PROVIDERS[_DEFAULT_PROVIDER]
    return (
        os.environ.get(p["api_key_env"], "").strip(),
        custom_base or p["base_url"],
        custom_model or p["default_model"],
    )


def _get_provider_label() -> str:
    """返回当前生效 provider 的可读名称（用于 fallback 消息）"""
    provider_name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider_name in PROVIDERS:
        return PROVIDERS[provider_name]["label"]

    for name, cfg in PROVIDERS.items():
        if os.environ.get(cfg["api_key_env"], "").strip():
            return cfg["label"]
    return PROVIDERS[_DEFAULT_PROVIDER]["label"]


def _get_api_key_env() -> str:
    """返回当前生效 provider 的 API Key 环境变量名"""
    provider_name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider_name in PROVIDERS:
        return PROVIDERS[provider_name]["api_key_env"]

    for name, cfg in PROVIDERS.items():
        if os.environ.get(cfg["api_key_env"], "").strip():
            return cfg["api_key_env"]
    return PROVIDERS[_DEFAULT_PROVIDER]["api_key_env"]


def call_llm(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
    response_format: dict | None = None,
) -> str | None:
    """
    调用 LLM API。

    Args:
        messages: [{"role": "user"/"system"/"assistant", "content": "..."}]
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        model: 模型名，默认使用环境变量 LLM_MODEL 或 provider 默认
        response_format: 如 {"type": "json_object"} 强制 JSON 输出

    Returns:
        str: LLM 返回的文本
        None: 调用失败（未配置 Key / 网络错误 / 限流等）。
              调用方必须处理 None —— 历史上本函数失败时返回"伪装成功"的
              回退文案，导致重试机制失效、坏数据被当作正常结果落盘。
              面向用户的友好提示由 web 边界用 _fallback_response 生成。
    """
    api_key, base_url, default_model = _resolve_provider()
    model = model or default_model

    if not api_key:
        key_env = _get_api_key_env()
        logger.warning(f"{key_env} 未设置，LLM 调用不可用")
        return None

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.environ.get("LLM_TIMEOUT", "60")),
        )

        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        logger.debug(f"LLM 调用成功: {len(content)} 字, model={model}")
        return content

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return None


def call_llm_stream(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
):
    """
    流式调用 LLM API。

    Yields:
        str: 每个 chunk 的文本片段
    """
    api_key, base_url, default_model = _resolve_provider()
    model = model or default_model

    if not api_key:
        yield _fallback_response(messages)
        return

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.environ.get("LLM_TIMEOUT", "60")),
        )

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    except Exception as e:
        logger.error(f"LLM 流式调用失败: {e}")
        yield f"[LLM 调用失败: {e}]"


def _fallback_response(
    messages: list[dict], response_format: dict | None = None
) -> str:
    """API 不可用时的回退回复"""
    label = _get_provider_label()
    key_env = _get_api_key_env()
    last_msg = messages[-1]["content"] if messages else ""
    if response_format and response_format.get("type") == "json_object":
        return json.dumps({
            "summary": f"（回退模式）{label} API 密钥未配置。请设置 {key_env} 环境变量。提问：{last_msg[:50]}",
            "characters": [],
            "events": [],
        }, ensure_ascii=False)
    return (
        f"【系统提示】当前 LLM API ({label}) 未配置。请设置 {key_env} 环境变量后重试。\n"
        f"您的问题是：{last_msg[:200]}"
    )




if __name__ == "__main__":
    # 简单测试
    resp = call_llm([{"role": "user", "content": "你好"}])
    print(f"测试回复: {resp[:100]}")
