"""
逐章编译Wiki（v2 — 全优化版）
================================
修复：断点续传、语义分块、冲突规整、LLM重试、并发控制、token预算、实体校验、原子写入

变更记录:
    v1: 旧版 [:3000] 截断 + 长度断点 + 串行调用
    v2: chapter_id 断点 + 语义分块 + 冲突规整 + 并发 + 预算控制 + 配置外置
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.llm import call_llm
from core.compiler_config import get_config

logger = logging.getLogger(__name__)

# ── 数据格式版本号 ─────────────────────────────────────────────────────
# 每次修改产出数据结构时递增此版本号
# load_wiki() 检查版本兼容性，不兼容时抛出警告
DATA_FORMAT_VERSION = 1


# ═══════════════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════════════

def _atomic_write(data: Any, filepath: str) -> None:
    """原子写入：临时文件 → os.replace，防止半残文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        dir=os.path.dirname(filepath),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _backup(filepath: str) -> str | None:
    """生成备份文件，返回备份路径。原文件不存在时返回 None。"""
    if not os.path.exists(filepath):
        return None
    bak_path = filepath + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, bak_path)
    # 清理旧备份，保留最近 3 个
    base = filepath + ".bak."
    backups = sorted([p for p in Path(os.path.dirname(filepath)).glob(os.path.basename(base) + "*")])
    while len(backups) > 3:
        backups.pop(0).unlink()
    return bak_path


def _semantic_split(text: str, max_chars: int = 2800) -> list[str]:
    """
    语义分块：按段落/对话边界切割，不打断完整段落和对话。

    策略:
        1. 先按 \n\n 分段落
        2. 合并非空段落到目标块大小
        3. 保证每块在段落边界切割
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text[:max_chars]] if text else []

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 如果单段落超过 max_chars，强制拆分（按句子）
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            sentences = re.split(r"(?<=[。！？\n])", para)
            for sent in sentences:
                if not sent.strip():
                    continue
                if len(current) + len(sent) > max_chars and current:
                    chunks.append(current)
                    current = sent
                else:
                    current += sent
            continue

        # 正常合并
        if len(current) + len(para) > max_chars and current:
            chunks.append(current)
            current = para
        else:
            current = (current + "\n\n" + para) if current else para

    if current:
        chunks.append(current)

    return chunks


def _call_llm_with_retry(
    messages: list[dict],
    max_retries: int | None = None,
    retry_delay: float | None = None,
) -> str | None:
    """
    LLM 调用带指数退避重试。

    Returns:
        str: LLM 返回文本
        None: 全部重试失败（call_llm 失败时返回 None，触发退避重试）
    """
    cfg = get_config()
    max_retries = max_retries if max_retries is not None else cfg["max_retries"]
    base_delay = retry_delay if retry_delay is not None else cfg["retry_base_delay"]

    for attempt in range(max_retries):
        try:
            response = call_llm(messages)
        except Exception as e:
            logger.warning(f"    LLM 调用异常: {e}")
            response = None

        if response and response.strip():
            return response

        # 失败（None / 空响应）→ 指数退避后重试
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.info(f"    LLM 无有效响应，等待 {delay:.0f}s 后重试 ({attempt + 1}/{max_retries})...")
            time.sleep(delay)
        else:
            logger.error(f"    LLM 调用失败（已重试 {max_retries} 次）")

    return None


def _parse_llm_json(response: str) -> dict | None:
    """从 LLM 响应中提取并解析首个 JSON 对象（括号平衡解析，容忍尾随文字）"""
    if not response:
        return None
    start = response.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(response[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


# LLM 降级文案标记 —— 这类内容绝不能被当作真实提取结果落盘
_FALLBACK_MARKERS = ("（回退模式）", "【系统提示】当前 LLM API", "API 密钥未配置")


def _is_fallback_text(text: str) -> bool:
    """检测 LLM 降级/错误提示文案"""
    return any(m in text for m in _FALLBACK_MARKERS)


def _entry_has_content(entry: dict) -> bool:
    """判断编译结果是否含有效内容（区别于 LLM 故障产生的空占位条目）"""
    return bool(entry.get("characters") or entry.get("events") or entry.get("relationships"))


def _validate_entity(entity: dict, known_names: set | None = None) -> bool:
    """
    校验单个人物条目是否合理。
    过滤明显幻觉（空名字、纯符号、过于生僻的组合）。
    """
    name = entity.get("name", "").strip()
    if not name or len(name) < 2 or len(name) > 20:
        return False
    # 纯标点符号/数字
    if re.match(r"^[\s\d,，。、！？\-\+\=\.\*#@]+$", name):
        return False
    # 纯英文（网文极少纯英文人名）
    if re.match(r"^[a-zA-Z\s\.]+$", name) and len(name) > 4:
        # 允许 "Zhang San" 类带空格的英文，禁止无意义
        if not re.search(r"[A-Z]", name):
            return False
    # 如果提供了已知人名库，做模糊匹配
    if known_names:
        # 完全不在知识库中且字数 >= 4 的陌生名标记为可疑
        if len(name) >= 4 and not any(kw in name for kw in ["第", "章", "回"]):
            pass  # 不做硬过滤，仅提示
    return True


def _validate_relationship(rel: dict) -> bool:
    """校验单条关系是否合理"""
    source = rel.get("source", "").strip()
    target = rel.get("target", "").strip()
    if not source or not target or source == target:
        return False
    relation = rel.get("relation", "").strip()
    if not relation or len(relation) < 2:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════
#  Token 预算控制
# ═══════════════════════════════════════════════════════════════════════

class TokenBudget:
    """单本书 token 预算控制器"""

    def __init__(self, max_tokens: int | None = None):
        cfg = get_config()
        self.max_tokens = max_tokens if max_tokens is not None else cfg["max_tokens_per_book"]
        self._spent = 0
        self._frozen = False

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def remaining(self) -> int:
        if self.max_tokens is None:
            return float("inf")  # type: ignore
        return max(0, self.max_tokens - self._spent)

    @property
    def exhausted(self) -> bool:
        if self.max_tokens is None:
            return False
        return self._spent >= self.max_tokens

    def record(self, estimated_tokens: int):
        """记录 token 消耗（估算）"""
        self._spent += estimated_tokens
        if self.exhausted:
            logger.warning(f"  [TokenBudget] ⚠️ Token 预算已耗尽（{self._spent}/{self.max_tokens}），编译终止")
            self._frozen = True

    def assert_available(self):
        if self._frozen or self.exhausted:
            raise TokenBudgetExhausted(
                f"Token 预算已耗尽（{self._spent}/{self.max_tokens}）"
            )


class TokenBudgetExhausted(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════
#  Prompt 模板
# ═══════════════════════════════════════════════════════════════════════

CHAPTER_WIKI_PROMPT = """你是一个网络小说分析专家。请分析以下章节内容，提取结构化信息。

章节标题：{chapter_title}

章节内容：
{chapter_text}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "summary": "本章摘要（100-200字）",
    "characters": [
        {{
            "name": "人物本名（全书最通用的官方全名）",
            "role": "主角/配角/路人",
            "description": "本章中该人物的表现",
            "aliases": ["本章中对该人物的其他称呼，如官职、绰号、小名等"]
        }}
    ],
    "events": ["关键事件1", "关键事件2"],
    "relationships": [
        {{"source": "人物A（使用本名）", "target": "人物B（使用本名）", "relation": "关系描述"}}
    ]
}}

要求：
1. characters 只列出本章确实出场或被提及的人物
2. 同一人物在全书中有多个称呼（本名、官职、绰号、代称等），请用 name 字段填写**最通用的官方全名**，将所有其他称呼填入 aliases 数组
3. relationships 的 source 和 target 一律使用 name 中的本名，不要用别名
4. events 按时间顺序排列
5. summary 要包含本章最关键的情节推进
"""

MERGE_PROMPT = """你是一个网文分析专家。以下是同一章节拆分为多个片段后分别提取的结构化信息，请合并为一份统一的条目。

要求：
1. 合并出现在多个片段中的同一个人物（统一称谓），选择出现次数最多的角色描述
2. 如果不同片段对同一关系的描述有冲突（如一块写"朋友"另一块写"敌人"），保留权重更高的
3. 合并人物的 aliases 数组，去重
4. 合并事件列表，去重并按时间/逻辑顺序排列
5. 生成一份简洁的章节摘要（150-200字）

请以JSON格式返回：
{{
    "summary": "合并后的摘要",
    "characters": [
        {{
            "name": "人物本名",
            "role": "角色类型",
            "description": "描述",
            "aliases": ["别名1", "别名2"]
        }}
    ],
    "events": ["事件1", "事件2"],
    "relationships": [
        {{"source": "人物A", "target": "人物B", "relation": "关系描述"}}
    ]
}}

多个片段提取结果：
{chunks_data}
"""


# ═══════════════════════════════════════════════════════════════════════
#  单章编译
# ═══════════════════════════════════════════════════════════════════════

def parse_chapter(
    chapter_title: str,
    chapter_text: str,
    chapter_index: int = 0,
    budget: TokenBudget | None = None,
) -> dict:
    """
    编译单章为 Wiki 条目。

    长章节语义分块 → 并发提取 → 冲突规整 → 返回。
    """
    cfg = get_config()
    max_chars = cfg["chunk_max_chars"]
    max_sub_chunks = cfg["max_sub_chunks_per_chapter"]

    if not chapter_text or len(chapter_text.strip()) < 10:
        return {
            "summary": chapter_title,
            "characters": [],
            "events": [],
            "relationships": [],
        }

    # 短章节直接处理
    if len(chapter_text) <= max_chars:
        result = _parse_single_chunk(chapter_title, chapter_text)
        if budget:
            budget.record(len(chapter_text) // 2)
        return result

    # 长章节：语义分块
    chunks = _semantic_split(chapter_text, max_chars)

    # 限制分块数量
    if len(chunks) > max_sub_chunks:
        logger.info(f"  [分块限制] {chapter_title}: {len(chunks)} 块压缩至 {max_sub_chunks} 块")
        # 合并多余的块
        merged_extra = "\n\n".join(chunks[max_sub_chunks - 1:])
        chunks = chunks[:max_sub_chunks - 1] + [merged_extra]

    # 并发提取各块
    chunk_results: list[dict] = []
    failed_chunks: list[int] = []

    with ThreadPoolExecutor(max_workers=cfg["max_concurrency"]) as executor:
        future_map = {
            executor.submit(
                _parse_single_chunk,
                f"{chapter_title}[{i+1}/{len(chunks)}]",
                chunks[i],
            ): i for i in range(len(chunks))
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                result = future.result()
                if result and result.get("characters"):
                    chunk_results.append(result)
                    if budget:
                        budget.record(len(chunks[idx]) // 2)
                else:
                    failed_chunks.append(idx)
            except TokenBudgetExhausted:
                raise
            except Exception as e:
                logger.warning(f"    子块 {idx+1} 提取异常: {e}")
                failed_chunks.append(idx)

    # 如果有失败的块，用空结果占位
    for idx in failed_chunks:
        chunk_results.append({
            "summary": f"[片段{idx+1}]",
            "characters": [],
            "events": [],
            "relationships": [],
        })

    if not chunk_results or all(not r.get("characters") for r in chunk_results):
        return {
            "summary": chapter_title,
            "characters": [],
            "events": [],
            "relationships": [],
        }

    # LLM 合并规整（消弭冲突、统一称谓）
    merged = _llm_merge_chunks(chapter_title, chunk_results)

    # 预算记录（合并调用）
    if budget:
        budget.record(500)

    return merged


def _parse_single_chunk(chapter_title: str, chapter_text: str) -> dict:
    """单段 LLM 提取"""
    cfg = get_config()
    prompt = CHAPTER_WIKI_PROMPT.format(
        chapter_title=chapter_title,
        chapter_text=chapter_text,
    )

    response = _call_llm_with_retry([{"role": "user", "content": prompt}])
    if response and not _is_fallback_text(response):
        data = _parse_llm_json(response)
        if data and "summary" in data and "characters" in data:
            return data

    # 返回空结果
    return {
        "summary": chapter_title,
        "characters": [],
        "events": [],
        "relationships": [],
    }


def _llm_merge_chunks(chapter_title: str, chunk_results: list[dict]) -> dict:
    """
    用 LLM 合并多块提取结果，消弭冲突、统一称谓。

    如果 LLM 合并失败，回退到简单合并。
    """
    cfg = get_config()

    chunks_data_text = json.dumps(
        [
            {
                "summary": r.get("summary", "")[:100],
                "characters": r.get("characters", []),
                "events": r.get("events", []),
                "relationships": r.get("relationships", []),
            }
            for r in chunk_results
        ],
        ensure_ascii=False,
        indent=2,
    )

    prompt = MERGE_PROMPT.format(chunks_data=chunks_data_text)
    response = _call_llm_with_retry([{"role": "user", "content": prompt}])

    if response:
        data = _parse_llm_json(response)
        if data and "summary" in data and "characters" in data:
            return data

    # 回退：简单合并
    return _simple_merge(chunk_results, chapter_title, cfg)


def _simple_merge(chunk_results: list[dict], chapter_title: str, cfg: dict) -> dict:
    """简单合并（去重 + 拼接），不依赖 LLM"""
    seen_names = set()
    seen_events = set()
    seen_rels = set()
    merged = {
        "summary": "",
        "characters": [],
        "events": [],
        "relationships": [],
    }

    for r in chunk_results:
        # 人物去重 + 别名合并
        for c in r.get("characters", []):
            name = c.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                if _validate_entity(c):
                    merged["characters"].append(c)
            elif name and name in seen_names:
                # 合并 aliases（同一人物在不同块可能有不同别名）
                existing = next((x for x in merged["characters"] if x.get("name") == name), None)
                if existing:
                    existing_aliases = set(existing.get("aliases", []) or [])
                    new_aliases = set(c.get("aliases", []) or [])
                    merged_aliases = existing_aliases | new_aliases
                    if merged_aliases:
                        existing["aliases"] = sorted(merged_aliases)

        # 事件去重
        for e in r.get("events", []):
            if e and e not in seen_events:
                seen_events.add(e)
                merged["events"].append(e)

        # 关系去重
        for rel in r.get("relationships", []):
            key = (rel.get("source", ""), rel.get("target", ""))
            if key[0] and key[1] and key not in seen_rels:
                seen_rels.add(key)
                if _validate_relationship(rel):
                    merged["relationships"].append(rel)

    # 摘要拼接
    summaries = [r.get("summary", "") for r in chunk_results if r.get("summary")]
    full_summary = " ".join(summaries)
    if len(full_summary) > cfg["summary_compress_threshold"]:
        merged["summary"] = _summarize_text(
            chapter_title, full_summary, cfg["summary_compress_target"]
        )
    else:
        merged["summary"] = full_summary

    return merged


def _summarize_text(chapter_title: str, text: str, target_len: int) -> str:
    """用 LLM 压缩摘要"""
    prompt = f"""请将以下章节分析摘要压缩为 {target_len} 字以内的简洁版本，保留核心情节和关键事件：

{text}

以 JSON 格式返回：
{{"summary": "压缩后的摘要"}}"""
    response = _call_llm_with_retry([{"role": "user", "content": prompt}])
    if response:
        data = _parse_llm_json(response)
        if data and data.get("summary"):
            return data["summary"]
    return text[:target_len]


# ═══════════════════════════════════════════════════════════════════════
#  断点管理
# ═══════════════════════════════════════════════════════════════════════

class CheckpointManager:
    """
    断点管理器。

    存储已完成 chapter_index 集合而非数组长度，避免脏数据跳过。
    支持多阶段断点（wiki/卷摘要/全书/图谱）。

    文件格式:
        {checkpoint_dir}/{prefix}_checkpoint.json
        {
            "completed_indices": [0, 1, 2, ...],  # 已完成的 chapter_index
            "phase": "wiki",                       # 当前阶段: wiki/volume/book/graph
            "updated_at": "2026-01-01T00:00:00",
            "stats": {...}
        }
    """

    def __init__(self, novel_key: str, checkpoint_dir: str = ""):
        self.novel_key = novel_key
        if not checkpoint_dir:
            cfg = get_config()
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            checkpoint_dir = os.path.join(base, "data", "checkpoints")
        self._dir = checkpoint_dir
        os.makedirs(self._dir, exist_ok=True)
        self._cache: dict[str, set[int]] = {}

    def _path(self, phase: str) -> str:
        return os.path.join(self._dir, f"{self.novel_key}_{phase}_checkpoint.json")

    def _load(self, phase: str) -> set[int]:
        if phase in self._cache:
            return self._cache[phase]
        path = self._path(phase)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                indices = set(data.get("completed_indices", []))
                self._cache[phase] = indices
                return indices
            except Exception:
                pass
        self._cache[phase] = set()
        return set()

    def _save(self, phase: str, indices: set[int], stats: dict | None = None):
        self._cache[phase] = indices
        data = {
            "novel": self.novel_key,
            "phase": phase,
            "completed_indices": sorted(indices),
            "updated_at": datetime.now().isoformat(),
            "stats": stats or {},
        }
        _atomic_write(data, self._path(phase))

    def is_completed(self, chapter_index: int, phase: str = "wiki") -> bool:
        return chapter_index in self._load(phase)

    def mark_completed(self, chapter_index: int, phase: str = "wiki", stats: dict | None = None):
        indices = self._load(phase)
        indices.add(chapter_index)
        self._save(phase, indices, stats)

    def get_completed(self, phase: str = "wiki") -> set[int]:
        return self._load(phase)

    def all_completed(self, total_indices: set[int], phase: str = "wiki") -> bool:
        completed = self._load(phase)
        return total_indices.issubset(completed)

    def reset(self, phase: str | None = None):
        if phase:
            path = self._path(phase)
            if os.path.exists(path):
                os.remove(path)
            self._cache.pop(phase, None)
        else:
            for key in list(self._cache.keys()):
                self.reset(key)

    def get_phase_status(self) -> dict:
        """获取各阶段进度"""
        phases = ["wiki", "volume", "book", "graph"]
        status = {}
        for p in phases:
            indices = self._load(p)
            status[p] = {
                "completed": len(indices),
                "indices": sorted(indices)[:10],  # 只显示前10
            }
        return status


# ═══════════════════════════════════════════════════════════════════════
#  子块级缓存（新增 1）
# ═══════════════════════════════════════════════════════════════════════

class SubChunkCache:
    """
    子块断点缓存。
    超长章节拆分为多个子块分别调用 LLM，每块完成即落地缓存。
    崩溃后只重跑未完成的子块。

    存储路径: data/checkpoints/{novel_key}/subchunks/{chapter_index}.json
    格式: { "completed": [0, 1, 2], "results": [{...}, {...}, null] }
    """

    def __init__(self, novel_key: str):
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "checkpoints", novel_key, "subchunks"
        )
        self._dir = base
        os.makedirs(self._dir, exist_ok=True)
        self._cache: dict[int, dict] = {}

    def _path(self, chapter_index: int) -> str:
        return os.path.join(self._dir, f"{chapter_index}.json")

    def init_chapter(self, chapter_index: int, total_subchunks: int):
        """初始化一章的子块缓存"""
        data = {
            "chapter_index": chapter_index,
            "total": total_subchunks,
            "completed": [],
            "results": [None] * total_subchunks,
        }
        _atomic_write(data, self._path(chapter_index))
        self._cache[chapter_index] = data

    def mark_subchunk_done(
        self, chapter_index: int, subchunk_idx: int, result: dict
    ):
        """标记单个子块完成并缓存结果"""
        path = self._path(chapter_index)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = self._cache.get(chapter_index, {
                "chapter_index": chapter_index,
                "total": 0, "completed": [], "results": [],
            })

        if subchunk_idx not in data["completed"]:
            data["completed"].append(subchunk_idx)
        data["results"][subchunk_idx] = result
        _atomic_write(data, path)
        self._cache[chapter_index] = data

    def get_incomplete(self, chapter_index: int, total: int | None = None) -> list[int]:
        """获取未完成的子块索引列表"""
        path = self._path(chapter_index)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[chapter_index] = data
            completed = set(data.get("completed", []))
            total = data.get("total", total or len(completed))
        else:
            if total is None:
                return list(range(total)) if total else []
            completed = set()

        if total is None:
            return []
        return [i for i in range(total) if i not in completed]

    def get_cached_results(self, chapter_index: int) -> list[dict | None]:
        """获取一章的所有缓存子块结果"""
        path = self._path(chapter_index)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("results", [])
        return []

    def cleanup(self, chapter_index: int):
        """编译完成后清理子块缓存"""
        path = self._path(chapter_index)
        if os.path.exists(path):
            os.remove(path)
        self._cache.pop(chapter_index, None)


# ═══════════════════════════════════════════════════════════════════════
#  失败章节隔离（新增 2）
# ═══════════════════════════════════════════════════════════════════════

class FailedChaptersManager:
    """
    失败章节清单。
    LLM 多次重试仍失败的章节记录到独立文件，不阻塞编译流程。
    编译完成后可查看失败列表，支持单章重试。

    存储路径: data/checkpoints/{novel_key}/failed_chapters.json
    格式: { "failed": [{"index": 5, "title": "第五章", "error": "..."}], ... }
    """

    def __init__(self, novel_key: str):
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "checkpoints", novel_key
        )
        self._path = os.path.join(base, "failed_chapters.json")
        os.makedirs(base, exist_ok=True)
        self._failed: list[dict] = []
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._failed = data.get("failed", [])
            except Exception:
                self._failed = []

    def _save(self):
        _atomic_write({"failed": self._failed, "total": len(self._failed)}, self._path)

    def add(self, chapter_index: int, title: str, error: str):
        """记录一条失败"""
        # 去重
        self._failed = [f for f in self._failed if f.get("index") != chapter_index]
        self._failed.append({
            "index": chapter_index,
            "title": title,
            "error": str(error),
            "timestamp": datetime.now().isoformat(),
        })
        self._save()

    def remove(self, chapter_index: int):
        """移除一条失败（重试成功后）"""
        self._failed = [f for f in self._failed if f.get("index") != chapter_index]
        self._save()

    def get_all(self) -> list[dict]:
        return list(self._failed)

    def get_summary(self) -> str:
        if not self._failed:
            return "无失败章节"
        lines = [f"失败章节 ({len(self._failed)} 章):"]
        for f in self._failed:
            lines.append(f"  [{f['index']}] {f['title']}: {str(f['error'])[:60]}")
        return "\n".join(lines)

    def retry_chapter(
        self, chapter_index: int, chapter_title: str, chapter_text: str,
        budget: TokenBudget | None = None,
    ) -> dict | None:
        """重试单章编译。成功则从失败清单移除。"""
        from core.chapter_parser import parse_chapter as _parse
        try:
            result = _parse(chapter_title, chapter_text, chapter_index, budget)
            if result and result.get("characters"):
                self.remove(chapter_index)
                return result
        except Exception as e:
            self.add(chapter_index, chapter_title, str(e))
        return None


# ═══════════════════════════════════════════════════════════════════════
#  增量编译模式（新增 3）
# ═══════════════════════════════════════════════════════════════════════

def build_wiki_incremental(
    novel_data: dict,
    checkpoint_path: str | None = None,
    novel_key: str = "",
    new_chapters: list[int] | None = None,
) -> list[dict]:
    """
    增量编译：只处理新增/修改章节，复用已有断点结果。

    参数:
        new_chapters: 指定要编译的 chapter_index 列表。
                      为 None 时自动检测「已有断点 vs 现有数据」差异。
    返回:
        全量 wiki_entries（包含新旧）
    """
    cpm = CheckpointManager(novel_key)
    chapters = novel_data["chapters"]

    # 加载已有条目
    wiki_entries: list[dict] = []
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                wiki_entries = json.load(f)
        except Exception:
            wiki_entries = []

    # 自动检测新增章节
    if new_chapters is None:
        completed = cpm.get_completed("wiki")
        total = len(chapters)
        new_chapters = [i for i in range(total) if i not in completed]

    if not new_chapters:
        logger.info("[增量模式] 无新增章节，跳过")
        return wiki_entries

    logger.info(f"[增量模式] 检测到 {len(new_chapters)} 章待编译: {new_chapters[:5]}...")
    budget = TokenBudget()

    for i in new_chapters:
        if i < 0 or i >= len(chapters):
            continue
        ch = chapters[i]
        ch_title = ch.get("title", ch.get("chapter_title", f"第{i}章"))
        ch_text = ch.get("text", "")

        logger.info(f"  [增量 {i}] {ch_title}...")
        try:
            entry = parse_chapter(ch_title, ch_text, chapter_index=i, budget=budget)
            # 更新或插入
            existing_idx = next(
                (pos for pos, e in enumerate(wiki_entries) if e.get("chapter_index") == i),
                None,
            )
            entry["chapter_index"] = i
            entry["chapter_title"] = ch_title
            if existing_idx is not None:
                wiki_entries[existing_idx] = entry
            else:
                insert_idx = next(
                    (pos for pos, e in enumerate(wiki_entries)
                     if e.get("chapter_index", -1) > i),
                    len(wiki_entries),
                )
                wiki_entries.insert(insert_idx, entry)

            cpm.mark_completed(i, "wiki")
            if checkpoint_path:
                _atomic_write(wiki_entries, checkpoint_path)
        except TokenBudgetExhausted:
            break
        except Exception as e:
            logger.error(f"  ❌ [增量 {i}] 失败: {e}")
            continue

    logger.info(f"[增量模式] 完成: 处理 {len(new_chapters)} 章")
    return wiki_entries


# ═══════════════════════════════════════════════════════════════════════
#  整书编译（入口 — v2 更新版）
# ═══════════════════════════════════════════════════════════════════════

def build_wiki(
    novel_data: dict,
    batch_size: int | None = None,
    delay: int | None = None,
    checkpoint_path: str | None = None,
    novel_key: str = "",
    incremental: bool = False,
    new_chapters: list[int] | None = None,
    pause_check: Callable[[], bool] | None = None,
) -> list[dict]:
    """
    将整本小说逐章编译为 Wiki。

    优化点:
        1. chapter_id 断点而非长度
        2. 子块级断点 (SubChunkCache)
        3. 失败章节隔离 (FailedChaptersManager)
        4. 增量模式 (incremental=True)
        5. 并发控制 + token 预算
        6. 原子写入 + 备份
        7. 暂停/恢复 (pause_check 回调)
    """
    # 增量模式
    if incremental:
        return build_wiki_incremental(
            novel_data, checkpoint_path, novel_key, new_chapters,
        )

    cfg = get_config()
    batch_size = batch_size if batch_size is not None else cfg["batch_size"]
    delay = delay if delay is not None else cfg["batch_delay"]

    chapters = novel_data["chapters"]
    title = novel_data.get("title", novel_key)
    total_chapters = len(chapters)

    budget = TokenBudget()

    if not novel_key:
        novel_key = title
    cpm = CheckpointManager(novel_key)
    failed_mgr = FailedChaptersManager(novel_key)
    completed_indices = cpm.get_completed("wiki")

    start_idx = 1 if chapters and chapters[0].get("title", "") == "前言" else 0

    stats = {
        "total": total_chapters - start_idx,
        "resumed": len(completed_indices),
        "failed": 0,
        "skipped": 0,
    }

    logger.info(f"《{title}》共 {total_chapters} 章，")
    if completed_indices:
        logger.info(f"断点发现 {len(completed_indices)} 章已完成，继续...")
    else:
        logger.info(f"从头开始...")

    wiki_entries: list[dict] = []
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                wiki_entries = json.load(f)
        except Exception:
            wiki_entries = []

    batch_count = 0
    for i in range(start_idx, total_chapters):
        try:
            budget.assert_available()
        except TokenBudgetExhausted as e:
            logger.warning(f"  ⚠️ {e}")
            break

        # 暂停检查
        if pause_check and pause_check():
            logger.info(f"  ⏸️ 编译暂停（第 {i} 章）")
            # 保存当前进度
            if checkpoint_path and wiki_entries:
                _backup(checkpoint_path)
                _atomic_write(wiki_entries, checkpoint_path)
            # 等待恢复
            while pause_check and pause_check():
                time.sleep(1)
            logger.info(f"  ▶️ 编译恢复")

        if i in completed_indices:
            stats["skipped"] += 1
            continue

        ch = chapters[i]
        ch_title = ch.get("title", ch.get("chapter_title", f"第{i}章"))
        ch_text = ch.get("text", "")
        logger.info(f"  [{i}/{total_chapters - 1}] {ch_title}...")

        try:
            # 检测是否超长（需要子块断点）
            if len(ch_text) > cfg["chunk_max_chars"]:
                entry = _parse_chapter_with_subchunks(
                    ch_title, ch_text, i, novel_key, budget,
                )
            else:
                entry = parse_chapter(ch_title, ch_text, chapter_index=i, budget=budget)
        except TokenBudgetExhausted as e:
            logger.warning(f"  ⚠️ {e}")
            break
        except Exception as e:
            logger.error(f"  ❌ 编译失败 [{i}]: {e}")
            entry = {
                "summary": ch_title,
                "characters": [],
                "events": [],
                "relationships": [],
            }

        # 结果有效性判定:
        #   原文极短（空章节）→ 属正常，直接视为完成；
        #   有原文但提取为空 → LLM 故障，记入失败清单、不写断点，留待下次重试
        if len(ch_text.strip()) < 10 or _entry_has_content(entry):
            failed_mgr.remove(i)
            entry_ok = True
        else:
            stats["failed"] += 1
            failed_mgr.add(i, ch_title, "LLM 提取结果为空（可能 API 故障或限流）")
            entry_ok = False

        entry["chapter_index"] = i
        entry["chapter_title"] = ch_title

        # 上次失败留下的同章占位条目 → 替换而非重复插入
        existing_pos = next(
            (pos for pos, e in enumerate(wiki_entries) if e.get("chapter_index") == i),
            None,
        )
        if existing_pos is not None:
            wiki_entries[existing_pos] = entry
        else:
            insert_idx = next(
                (pos for pos, e in enumerate(wiki_entries) if e.get("chapter_index", -1) > i),
                len(wiki_entries),
            )
            wiki_entries.insert(insert_idx, entry)

        if entry_ok:
            cpm.mark_completed(i, "wiki")
        stats_completed = cpm.get_completed("wiki")

        if checkpoint_path:
            _backup(checkpoint_path)
            _atomic_write(wiki_entries, checkpoint_path)
            logger.info(f"    ✓ checkpoint (进度: {len(stats_completed)}/{stats['total']})")

        batch_count += 1
        if batch_count % batch_size == 0 and i < total_chapters - 1:
            logger.info(f"  --- 批次 {batch_count} 章, 暂停 {delay}s ---")
            time.sleep(delay)

    # 统计
    logger.info(f"\n《{title}》Wiki 编译完成:")
    logger.info(f"  总章节: {stats['total']}")
    logger.info(f"  已编译: {len(cpm.get_completed('wiki'))}")
    logger.info(f"  失败: {stats['failed']}")
    logger.info(f"  跳过(断点): {stats['skipped']}")
    failed_list = failed_mgr.get_all()
    if failed_list:
        logger.warning(f"  失败章节 ({len(failed_list)} 章):")
        for f in failed_list:
            logger.warning(f"    [{f['index']}] {f['title']}: {f['error'][:80]}")
    if budget.spent:
        logger.info(f"  预估 Token: ~{budget.spent}")

    return wiki_entries


def _parse_chapter_with_subchunks(
    chapter_title: str,
    chapter_text: str,
    chapter_index: int,
    novel_key: str,
    budget: TokenBudget | None = None,
) -> dict:
    """
    带子块断点的长章节编译。

    1. 检测已有子块缓存，只跑未完成的子块
    2. 每块完成后落地缓存
    3. 全部完成后合并（用 LLM 规整）
    4. 清理子块缓存
    """
    cfg = get_config()
    max_chars = cfg["chunk_max_chars"]
    max_sub_chunks = cfg["max_sub_chunks_per_chapter"]

    chunks = _semantic_split(chapter_text, max_chars)
    if len(chunks) > max_sub_chunks:
        merged_extra = "\n\n".join(chunks[max_sub_chunks - 1:])
        chunks = chunks[:max_sub_chunks - 1] + [merged_extra]

    # 子块缓存
    cache = SubChunkCache(novel_key)
    incomplete = cache.get_incomplete(chapter_index, len(chunks))

    if len(incomplete) < len(chunks):
        logger.info(f"    子块断点: {len(chunks) - len(incomplete)}/{len(chunks)} 块已缓存")
    else:
        cache.init_chapter(chapter_index, len(chunks))

    # 只跑未完成的子块
    results: list[dict | None] = cache.get_cached_results(chapter_index)

    if incomplete:
        with ThreadPoolExecutor(max_workers=cfg["max_concurrency"]) as executor:
            future_map = {}
            for idx in incomplete:
                future = executor.submit(
                    _parse_single_chunk,
                    f"{chapter_title}[{idx+1}/{len(chunks)}]",
                    chunks[idx],
                )
                future_map[future] = idx

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                    if result and result.get("characters"):
                        cache.mark_subchunk_done(chapter_index, idx, result)
                        if budget:
                            budget.record(len(chunks[idx]) // 2)
                    else:
                        # 失败也写空结果，避免重跑
                        cache.mark_subchunk_done(chapter_index, idx, {
                            "summary": f"[片段{idx+1}]",
                            "characters": [],
                            "events": [],
                            "relationships": [],
                        })
                except TokenBudgetExhausted:
                    raise
                except Exception as e:
                    logger.warning(f"    子块 {idx+1} 异常: {e}")
                    cache.mark_subchunk_done(chapter_index, idx, {
                        "summary": f"[片段{idx+1}]",
                        "characters": [], "events": [], "relationships": [],
                    })

        # 重新读取全部缓存结果
        results = cache.get_cached_results(chapter_index)

    valid_results = [r for r in results if r and r.get("characters")]
    if not valid_results:
        cache.cleanup(chapter_index)
        return {
            "summary": chapter_title,
            "characters": [], "events": [], "relationships": [],
        }

    # LLM 合并规整
    merged = _llm_merge_chunks(chapter_title, valid_results)

    # 清理缓存
    cache.cleanup(chapter_index)

    if budget:
        budget.record(500)

    return merged


# ═══════════════════════════════════════════════════════════════════════
#  产物增量更新（新增 4）
# ═══════════════════════════════════════════════════════════════════════

def update_volumes_incremental(
    wiki_entries: list[dict],
    changed_indices: set[int],
    existing_volumes: list[dict] | None = None,
    volume_size: int | None = None,
    novel_key: str = "",
) -> list[dict]:
    """
    增量更新卷摘要：只重新生成包含变更章节的卷，其他卷沿用现有摘要。
    """
    cfg = get_config()
    volume_size = volume_size if volume_size is not None else cfg["volume_size"]

    # 卷分组
    natural = _detect_natural_volumes(wiki_entries)
    if natural:
        vol_groups = natural
    else:
        vol_groups = []
        for i in range(0, len(wiki_entries), volume_size):
            end = min(i + volume_size - 1, len(wiki_entries) - 1)
            vol_groups.append((f"第{i+1}-{end+1}章", i, end))

    # 确定哪些卷需要刷新
    affected_volumes: set[int] = set()
    for vi, (_, start, end) in enumerate(vol_groups):
        if any(start <= ci <= end for ci in changed_indices):
            affected_volumes.add(vi)

    if not affected_volumes:
        logger.info("[增量卷摘要] 无变更章节影响卷摘要，跳过")
        return existing_volumes or []

    logger.info(f"[增量卷摘要] 需刷新 {len(affected_volumes)}/{len(vol_groups)} 卷")
    volumes = list(existing_volumes or [])

    for vi in affected_volumes:
        vol_name, start_idx, end_idx = vol_groups[vi]
        batch = wiki_entries[start_idx:end_idx + 1]
        start_ch = batch[0].get("chapter_index", start_idx + 1)
        end_ch = batch[-1].get("chapter_index", end_idx + 1)

        chapters_text = "\n".join(
            f"{w.get('chapter_title', '')}: {w.get('summary', '')}"
            for w in batch
        )
        prompt = f"""以下是小说第{start_ch}章到第{end_ch}章的章节摘要。
请生成一个 200 字左右的卷摘要，提炼这一卷的主要情节线、核心人物和关键事件。

{chapters_text}

以 JSON 格式返回：
{{"summary": "卷摘要", "main_characters": ["人物1", "人物2"]}}"""
        logger.info(f"  [增量] 刷新卷摘要 {vol_name}...")

        response = _call_llm_with_retry([{"role": "user", "content": prompt}])
        data = {"summary": response or "", "main_characters": []}
        if response:
            parsed = _parse_llm_json(response)
            if parsed:
                data = parsed

        entry = {
            "type": "volume",
            "volume_index": vi + 1,
            "title": vol_name,
            "chapter_range": [start_ch, end_ch],
            "summary": data.get("summary", response or ""),
            "main_characters": data.get("main_characters", []),
        }

        if vi < len(volumes):
            volumes[vi] = entry
        else:
            volumes.append(entry)

    logger.info(f"[增量卷摘要] 完成: 刷新 {len(affected_volumes)} 卷")
    return volumes


# ═══════════════════════════════════════════════════════════════════════
#  卷摘要 / 全书摘要 / 图谱（带断点 + 校验）
# ═══════════════════════════════════════════════════════════════════════

def _load_saved_hierarchical(novel_key: str) -> dict | None:
    """加载已保存的三层 Wiki（用于断点恢复时读回真实的卷/书摘要）"""
    if not novel_key:
        return None
    path = f"data/wiki/{novel_key}_hierarchical.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_volume_summaries(
    wiki_entries: list[dict],
    volume_size: int | None = None,
    novel_key: str = "",
):
    """
    构建卷摘要（自动检测自然卷 + 断点续跑）。
    """
    cfg = get_config()
    volume_size = volume_size if volume_size is not None else cfg["volume_size"]
    cpm = CheckpointManager(novel_key)

    natural_volumes = _detect_natural_volumes(wiki_entries)
    if natural_volumes:
        logger.info(f"检测到自然卷划分：共 {len(natural_volumes)} 卷")
        vol_groups = natural_volumes
    else:
        logger.info(f"未检测到自然卷，按每 {volume_size} 章人工分组...")
        vol_groups = []
        for i in range(0, len(wiki_entries), volume_size):
            end = min(i + volume_size - 1, len(wiki_entries) - 1)
            vol_groups.append((f"第{i+1}-{end+1}章", i, end))

    volumes = []
    # 断点恢复时优先从上次保存的文件读回真实卷摘要，避免 placeholder 假数据落盘
    saved_volumes = {}
    _saved = _load_saved_hierarchical(novel_key)
    if _saved:
        saved_volumes = {
            v.get("volume_index"): v for v in _saved.get("volumes", []) if isinstance(v, dict)
        }
    for vi, (vol_name, start_idx, end_idx) in enumerate(vol_groups):
        # 用起始章节索引作为 volume 断点标识
        vol_id = start_idx
        if cpm.is_completed(vol_id, "volume"):
            logger.info(f"  [断点] 卷 {vol_name} 已生成，跳过")
            prev = saved_volumes.get(vi + 1)
            if prev:
                volumes.append(prev)
            else:
                volumes.append({
                    "type": "volume",
                    "volume_index": vi + 1,
                    "title": vol_name,
                    "chapter_range": [start_idx + 1, end_idx + 1],
                    "summary": f"（卷 {vol_name}）",
                    "main_characters": [],
                })
            continue

        batch = wiki_entries[start_idx:end_idx + 1]
        start_ch = batch[0].get("chapter_index", start_idx + 1)
        end_ch = batch[-1].get("chapter_index", end_idx + 1)

        chapters_text = "\n".join([
            f"{w.get('chapter_title', '')}: {w.get('summary', '')}"
            for w in batch
        ])

        prompt = f"""以下是小说第{start_ch}章到第{end_ch}章的章节摘要。
请生成一个 200 字左右的卷摘要，提炼这一卷的主要情节线、核心人物和关键事件。

{chapters_text}

以 JSON 格式返回：
{{"summary": "卷摘要", "main_characters": ["人物1", "人物2"]}}"""
        logger.info(f"  生成卷摘要（{vol_name}，第{start_ch}-{end_ch}章）...")

        response = _call_llm_with_retry([{"role": "user", "content": prompt}])
        data = {"summary": response or "", "main_characters": []}
        if response:
            parsed = _parse_llm_json(response)
            if parsed:
                data = parsed

        volumes.append({
            "type": "volume",
            "volume_index": vi + 1,
            "title": vol_name,
            "chapter_range": [start_ch, end_ch],
            "summary": data.get("summary", response or ""),
            "main_characters": data.get("main_characters", []),
        })

        cpm.mark_completed(vol_id, "volume")

    logger.info(f"卷摘要生成完成：共 {len(volumes)} 卷")
    return volumes


def build_book_summary(
    wiki_entries: list[dict],
    volume_summaries: list[dict] | None = None,
    novel_key: str = "",
):
    """用 LLM 生成全书摘要（带断点）"""
    cpm = CheckpointManager(novel_key)
    if cpm.is_completed(0, "book"):
        logger.info("  [断点] 全书摘要已生成，跳过")
        # 从上次保存的文件读回真实全书摘要，避免恢复后全书摘要丢失
        _saved = _load_saved_hierarchical(novel_key)
        _book = (_saved or {}).get("book")
        if isinstance(_book, dict) and _book.get("summary"):
            return _book
        return None

    if volume_summaries:
        source = "\n".join(
            [f"卷{v['volume_index']}: {v['summary'][:200]}" for v in volume_summaries]
        )
    else:
        source = "\n".join([w.get("summary", "")[:200] for w in wiki_entries[:20]])

    prompt = f"""以下是这部小说的摘要信息，请生成一个 300 字左右的全书摘要，概括整体故事脉络：

{source}

以 JSON 格式返回：
{{"summary": "全书摘要", "main_characters": ["人物1", "人物2"], "themes": ["主题1", "主题2"]}}"""
    logger.info("  生成全书摘要...")

    response = _call_llm_with_retry([{"role": "user", "content": prompt}])
    data = {"summary": response or "", "main_characters": [], "themes": []}
    if response:
        parsed = _parse_llm_json(response)
        if parsed:
            data = parsed

    result = {
        "type": "book",
        "title": "全书总览",
        "summary": data.get("summary", response or ""),
        "main_characters": data.get("main_characters", []),
        "themes": data.get("themes", []),
    }

    cpm.mark_completed(0, "book")
    return result


def _detect_natural_volumes(wiki_entries: list[dict]) -> list | None:
    """检测章节标题中是否有自然卷划分"""
    volume_pattern = re.compile(r"(第[一二三四五六七八九十百千\d]+[卷部])")
    detected = []
    current_vol = None
    vol_start = 0

    for i, entry in enumerate(wiki_entries[:200]):
        title = entry.get("chapter_title", "") or ""
        match = volume_pattern.search(title)
        if match:
            vol_name = match.group(1)
            if vol_name != current_vol:
                if current_vol is not None:
                    detected.append((current_vol, vol_start, i - 1))
                current_vol = vol_name
                vol_start = i

    if current_vol is not None:
        detected.append((current_vol, vol_start, len(wiki_entries) - 1))

    if len(detected) >= 2:
        total_covered = sum(end - start + 1 for _, start, end in detected)
        if total_covered >= len(wiki_entries) * 0.5:
            return detected
    return None


# ═══════════════════════════════════════════════════════════════════════
#  保存/加载（原子写入 + 备份）
# ═══════════════════════════════════════════════════════════════════════

def save_wiki(wiki_entries: list[dict], filepath: str):
    """保存 Wiki（原子写入 + 自动备份 + 版本标记）"""
    _backup(filepath)
    data = {
        "_format_version": DATA_FORMAT_VERSION,
        "_generated_at": datetime.now().isoformat(),
        "entries": wiki_entries,
    }
    _atomic_write(data, filepath)
    logger.info(f"Wiki 已保存: {filepath} ({len(wiki_entries)} 条, v{DATA_FORMAT_VERSION})")


def save_hierarchical_wiki(
    chapter_entries: list[dict],
    volumes: list[dict],
    book: dict | None,
    filepath: str,
):
    """保存三层 Wiki（原子写入 + 备份 + 版本标记）"""
    data = {
        "_format_version": DATA_FORMAT_VERSION,
        "_generated_at": datetime.now().isoformat(),
        "book": book or {
            "type": "book",
            "title": "全书总览",
            "summary": "",
            "main_characters": [],
        },
        "volumes": volumes or [],
        "chapters": chapter_entries,
    }
    _backup(filepath)
    _atomic_write(data, filepath)
    logger.info(f"三层 Wiki 已保存: {filepath}")
    logger.info(f"  全书摘要: {'1 条' if book else '0 条'}")
    logger.info(f"  卷摘要: {len(volumes)} 条")
    logger.info(f"  章节摘要: {len(chapter_entries)} 条")


def load_wiki(filepath: str) -> dict:
    """从 JSON 文件加载 Wiki（兼容新旧格式 + 版本校验）"""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 新格式（v1+）：带 _format_version 字段
    if isinstance(raw, dict) and "_format_version" in raw:
        version = raw["_format_version"]
        if version > DATA_FORMAT_VERSION:
            logger.warning(f"  ⚠️ 数据格式版本({version})高于代码版本({DATA_FORMAT_VERSION})，可能存在兼容问题")
        chapters = raw.get("chapters") or raw.get("entries", [])
        return {
            "_format_version": version,
            "book": raw.get("book", {"type": "book", "title": "全书总览", "summary": "", "main_characters": []}),
            "volumes": raw.get("volumes", []),
            "chapters": chapters if isinstance(chapters, list) else [],
        }

    # 旧格式（dict 含 chapters）
    if isinstance(raw, dict) and "chapters" in raw:
        return raw

    # 旧格式（纯列表）
    if isinstance(raw, list):
        return {
            "book": {"type": "book", "title": "全书总览", "summary": "", "main_characters": []},
            "volumes": [],
            "chapters": raw,
        }
    return raw
