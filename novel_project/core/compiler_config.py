"""
编译管道配置（所有可调阈值外置）
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# ── 默认配置 ────────────────────────────────────────────────────────────
DEFAULTS = {
    # 单章处理
    "chunk_max_chars": 2800,         # 单块最大字符数（超过则分块）
    "chunk_min_chars": 200,          # 单块最小字符数（低于此值与前块合并）
    "max_sub_chunks_per_chapter": 8, # 单章最大分块数

    # LLM 调用
    "max_retries": 3,                # 单次 LLM 调用最大重试次数
    "retry_base_delay": 2.0,         # 重试基础等待秒数（指数退避乘数）
    "max_concurrency": 3,            # 最大并发 LLM 请求数
    "max_tokens_per_book": 2000000,  # 单本书最大 token 预算

    # 卷摘要
    "volume_size": 50,               # 每卷包含的章节数（无自然卷时）
    "volume_summary_max_chars": 200, # 卷摘要目标字数

    # 全书摘要
    "book_summary_max_chars": 300,   # 全书摘要目标字数

    # 编译批次
    "batch_size": 5,                 # 每批处理章数
    "batch_delay": 1.0,              # 每批间隔秒数

    # 分块提取后摘要压缩阈值
    "summary_compress_threshold": 500, # 超过此长度压缩
    "summary_compress_target": 200,    # 压缩目标长度

    # 实体校验
    "entity_min_mention": 2,         # 人物最少提及次数（低于此值过滤）
}


def load_config(path: str | None = None) -> dict:
    """加载配置，未指定路径时尝试从项目默认路径加载"""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".json":
                return {**DEFAULTS, **json.load(f)}
    return dict(DEFAULTS)


# ── 全局实例 ────────────────────────────────────────────────────────────
_config: dict | None = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: dict):
    global _config
    _config = {**get_config(), **cfg}
