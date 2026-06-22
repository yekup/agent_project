"""
LLM 统一调用层
==============
封装 OpenAI SDK 兼容的 API 调用。
支持 DeepSeek / OpenAI 等多种提供商。
"""
from __future__ import annotations

import json
import os
import time
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认使用 DeepSeek
DEFAULT_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def call_llm(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
    response_format: dict | None = None,
) -> str:
    """
    调用 LLM API。

    Args:
        messages: [{"role": "user"/"system"/"assistant", "content": "..."}]
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        model: 模型名，默认使用环境变量 LLM_MODEL 或 deepseek-chat
        response_format: 如 {"type": "json_object"} 强制 JSON 输出

    Returns:
        str: LLM 返回的文本
    """
    model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("DEEPSEEK_API_KEY", DEFAULT_API_KEY)

    if not api_key:
        logger.warning("DEEPSEEK_API_KEY 未设置，使用回退回复")
        return _fallback_response(messages, response_format)

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
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
        return _fallback_response(messages, response_format)


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
    model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("DEEPSEEK_API_KEY", DEFAULT_API_KEY)

    if not api_key:
        yield _fallback_response(messages)
        return

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
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
    last_msg = messages[-1]["content"] if messages else ""
    if response_format and response_format.get("type") == "json_object":
        return json.dumps({
            "summary": f"（回退模式）API 密钥未配置。请设置 DEEPSEEK_API_KEY 环境变量。提问：{last_msg[:50]}",
            "characters": [],
            "events": [],
        }, ensure_ascii=False)
    return (
        "【系统提示】当前 LLM API 未配置。请设置 DEEPSEEK_API_KEY 环境变量后重试。\n"
        f"您的问题是：{last_msg[:200]}"
    )


if __name__ == "__main__":
    # 简单测试
    resp = call_llm([{"role": "user", "content": "你好"}])
    print(f"测试回复: {resp[:100]}")
