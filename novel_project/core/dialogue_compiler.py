"""
对话 Wiki 编译模块
=================
把多 Agent 问答的会话内容蒸馏为结构化的「对话 Wiki」条目，
作为独立检索层接入现有三级检索系统。

红线:
  1. 独立分层 — 输出到 data/wiki/{novel}_dialogue.json，与章节 Wiki 物理隔离
  2. 审核准入 — 仅 review_result.passed == True 的问答允许编译
  3. 失败诚实 — LLM 失败返回 None，禁止伪装成功的占位条目落盘
  4. 原子写入 — 临时文件 + os.replace 模式
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.llm import call_llm

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WIKI_DIR = DATA_DIR / "wiki"

DIALOGUE_WIKI_FILENAME = "{novel}_dialogue.json"

# ── Prompt 模板（照抄方案，不得改写）───────────────────────────────────────

DIALOGUE_WIKI_PROMPT = """你是一个知识管理专家。以下是一段关于网络小说《{novel}》的问答讨论记录，已通过的讨论质量审核。请把这次讨论蒸馏为一条结构化的「讨论 Wiki」条目。

用户问题：{query}

讨论结论报告：
{report}

讨论中涉及的已知实体：{entities}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "topic": "讨论主题（20字以内）",
    "conclusion": "本次讨论得出的核心结论（200字以内，只写有原文依据的内容）",
    "key_points": ["支撑结论的关键要点，每条50字以内，最多5条"],
    "evidence_chapters": ["结论所依据的章节标题，从报告中提取，没有则为空数组"],
    "speculative": false
}}

要求：
1. conclusion 只能包含报告中有依据的结论，不得添加报告之外的知识
2. 如果结论主要是推测性的（假设、如果、可能），将 speculative 设为 true
3. evidence_chapters 只填报告里明确引用过的章节标题
4. 不要输出 JSON 以外的任何文字
"""

DIALOGUE_MERGE_PROMPT = """你是知识库维护专家。知识库中已有以下旧条目，现在来了一条新条目，请判断如何处理。

旧条目：
{old_entries}

新条目：
{new_entry}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "action": "merge | supersede | keep_both",
    "merged_entry": null,
    "reason": "一句话说明原因"
}}

判断规则：
1. merge —— 新旧条目讨论同一主题且结论互补：合并为一条，merged_entry 填入合并后的完整条目（保留双方 key_points 去重，conclusion 重写为统一表述，entities 并集）
2. supersede —— 新条目推翻了旧条目的结论：merged_entry 填入新条目，旧条目将被删除
3. keep_both —— 主题不同或各有价值：merged_entry 填 null，两条都保留
4. 拿不准时选 keep_both，宁可保留冗余，不可错误合并
"""


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _atomic_write(data: Any, filepath: str) -> None:
    """原子写入：临时文件 → os.replace，防止半残文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=os.path.dirname(filepath))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _backup(filepath: str) -> str | None:
    """生成备份，保留最近 3 个。参照 chapter_parser._backup。"""
    if not os.path.exists(filepath):
        return None
    import shutil
    bak_path = filepath + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, bak_path)
    base = os.path.basename(filepath) + ".bak."
    backup_dir = os.path.dirname(filepath)
    backups = sorted(
        [p for p in Path(backup_dir).glob(base + "*")],
        key=os.path.getmtime,
    )
    while len(backups) > 3:
        backups.pop(0).unlink()
    return bak_path


def _parse_llm_json(response: str) -> dict | None:
    """从 LLM 响应中提取 JSON，容忍尾随文字。参照 coordinator._extract_json_array。"""
    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return None


# ── 对话 Wiki 编译 ──────────────────────────────────────────────────────────

def compile_session(session_record: dict) -> dict | None:
    """
    把一条已通过的会话记录编译成结构化 Wiki 条目。

    准入门槛（任一不满足返回 None）：
      1. review_passed 为 True
      2. 匹配实体数 ≥ 2
      3. 报告中存在实质性内容（报告 ≥ 100 字）

    参数:
        session_record: memory.append_turn 落盘的会话记录 dict
            {session_id, novel, review_passed, turns: [{query, report, ts}],
             entities: [...], created_at: ...}

    返回:
        结构化条目 dict，失败返回 None
    """
    # ── 准入门槛 ──
    if not session_record.get("review_passed"):
        logger.info("[DialogueCompiler] compile_session 跳过：review 未通过")
        return None

    entities = session_record.get("entities", [])
    if len(entities) < 2:
        logger.info("[DialogueCompiler] compile_session 跳过：实体数=%d", len(entities))
        return None

    # 取最后一轮的 query 和 report
    turns = session_record.get("turns", [])
    if not turns:
        return None

    last_turn = turns[-1]
    query = last_turn.get("query", "")
    report = last_turn.get("report", "")

    if len(report) < 100:
        logger.info("[DialogueCompiler] compile_session 跳过：报告太短=%d", len(report))
        return None

    novel = session_record.get("novel", "未知小说")

    # ── LLM 编译 ──
    prompt = DIALOGUE_WIKI_PROMPT.format(
        novel=novel,
        query=query,
        report=report,
        entities="、".join(entities[:15]),
    )

    response = call_llm([{"role": "user", "content": prompt}])
    if response is None:
        logger.warning("[DialogueCompiler] compile_session 失败：LLM 返回 None")
        return None

    parsed = _parse_llm_json(response)
    if parsed is None:
        logger.warning("[DialogueCompiler] compile_session 失败：LLM 输出非 JSON")
        return None

    # ── 字段校验 ──
    topic = (parsed.get("topic") or "").strip()
    conclusion = (parsed.get("conclusion") or "").strip()
    key_points = parsed.get("key_points") or []
    evidence_chapters = parsed.get("evidence_chapters") or []
    speculative = bool(parsed.get("speculative", False))

    if not topic or not conclusion or not entities:
        logger.warning("[DialogueCompiler] compile_session 丢弃：必填字段为空")
        return None

    entry = {
        "id": f"dlg_{int(time.time())}_{session_record.get('session_id', 'unknown')[-6:]}",
        "topic": topic[:40],
        "conclusion": conclusion[:300],
        "key_points": [p[:80] for p in key_points[:5]],
        "entities": entities,
        "evidence_chapters": evidence_chapters,
        "speculative": speculative,
        "source_session": session_record.get("session_id", ""),
        "created_at": datetime.now().isoformat(),
    }

    return entry


# ── 冲突合并与持久化 ────────────────────────────────────────────────────────

def save_dialogue_wiki(novel: str, entry: dict) -> bool:
    """
    将编译后的对话 Wiki 条目写入独立文件。

    冲突合并流程:
      1. 加载 data/wiki/{novel}_dialogue.json
      2. 找出共享 ≥1 实体的旧条目（最多取 3 条）
      3. 有候选则调 DIALOGUE_MERGE_PROMPT 决定 merge/supersede/keep_both
      4. 无候选直接追加
      5. 原子写入 + 备份

    返回 True（写入成功）/ False（写入失败）
    """
    filepath = str(WIKI_DIR / DIALOGUE_WIKI_FILENAME.format(novel=novel))
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # 加载已有条目
    existing = {"entries": []}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {"entries": []}

    entries = existing.get("entries", [])
    if not isinstance(entries, list):
        entries = []

    # ── 冲突检测 ──
    entry_entities = set(entry.get("entities", []))
    candidates = []
    for old in entries:
        old_entities = set(old.get("entities", []))
        if entry_entities & old_entities:
            candidates.append(old)
        if len(candidates) >= 3:
            break

    if candidates:
        # ── LLM 冲突合并 ──
        merge_prompt = DIALOGUE_MERGE_PROMPT.format(
            old_entries=json.dumps(candidates, ensure_ascii=False, indent=2),
            new_entry=json.dumps(entry, ensure_ascii=False, indent=2),
        )
        response = call_llm([{"role": "user", "content": merge_prompt}])
        if response is None:
            logger.warning("[DialogueCompiler] 冲突合并跳过：LLM 返回 None")
            # 失败诚实 → 不写候选，直接追加
            entries.append(entry)
        else:
            merged_decision = _parse_llm_json(response)
            if merged_decision is None:
                logger.warning("[DialogueCompiler] 冲突合并跳过：LLM 输出非 JSON")
                entries.append(entry)
            else:
                action = merged_decision.get("action", "keep_both")
                if action == "merge":
                    merged_entry = merged_decision.get("merged_entry")
                    if merged_entry and isinstance(merged_entry, dict):
                        merged_entry.setdefault("id", entry["id"])
                        merged_entry.setdefault("created_at", entry["created_at"])
                        merged_entry.setdefault("source_session", entry["source_session"])
                        # 删除被合并的旧条目
                        old_ids = {c["id"] for c in candidates}
                        entries = [e for e in entries if e.get("id") not in old_ids]
                        entries.append(merged_entry)
                    else:
                        entries.append(entry)
                elif action == "supersede":
                    old_ids = {c["id"] for c in candidates}
                    entries = [e for e in entries if e.get("id") not in old_ids]
                    entries.append(entry)
                else:  # keep_both
                    entries.append(entry)
    else:
        entries.append(entry)

    # ── 原子写入 ──
    existing["entries"] = entries
    try:
        _backup(filepath)
        _atomic_write(existing, filepath)
        logger.info("[DialogueCompiler] 对话 Wiki 已保存: %s (%d 条)", filepath, len(entries))
        return True
    except Exception as e:
        logger.error("[DialogueCompiler] 对话 Wiki 写入失败: %s", e)
        return False
