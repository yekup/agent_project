"""
协调员 Agent — LangGraph 版本
=============================
使用 LangGraph StateGraph 替代硬编码 for 循环，
审核失败分类处理通过条件边路由，新增失败类型只需加节点。

图结构:
  detect_intent → decompose_task → research → write → review
    review → [passed]  → END
    review → [failed]   → handle_failure → research（循环至 max_rounds）
    review → [maxed out] → END (needs_manual_review)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Annotated, Any, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from core.llm import call_llm
from core.material_pool import MaterialPool
from core.security import audit_log

logger = logging.getLogger(__name__)


# ── 工具函数 ──

def _extract_json_array(text: str | None) -> list | None:
    """从 LLM 响应中提取首个 JSON 数组（括号平衡解析，容忍尾随文字）"""
    if not text:
        return None
    start = text.find("[")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj if isinstance(obj, list) else None
    except json.JSONDecodeError:
        return None


# ── Prompt ──

INTENT_PROMPT = """判断用户的问题属于哪种类型。
只返回一个词，不要其他文字。

类型说明：
- character: 分析单个人物（性格、形象、经历、评价）
- relationship: 分析两个以上人物的关系（关系变化、对比）
- summary: 总结类（全书画像、整体评价、排行榜）
- complex: 复杂推理（假设性问题、多步推理、需要综合分析）
- other: 其他

问题：{query}
"""

DECOMPOSE_PROMPT = """用户有一个{intent}类型的问题。
请把这个任务拆解为 2-4 个 Researcher 可以执行的检索步骤。

注意：Researcher 可以执行以下四类检索：
1. 搜索 Wiki（章节摘要和人物信息）
2. 搜索知识图谱（人物关系）
3. 搜索原文（章节正文）
4. 向量语义搜索（全文语义匹配，适合找不到确切关键词时使用）
不要假设 Researcher 可以访问互联网或其他外部资源。

问题：{query}

以 JSON 数组形式返回，每个步骤包含 step 和 description：
[{{"step": 1, "description": "..."}}]
"""


# ── LangGraph 状态定义 ──────────────────────────────────────────────

class CoordinatorState(TypedDict):
    """LangGraph 图状态"""
    query: str
    max_rounds: int
    intent: str
    steps: list[dict]
    completed_steps: list[dict]
    round_num: int
    draft: str
    review: dict
    done: bool
    # 材料池序列化字段（MaterialPool 不能直接放 state）
    _materials_text: str  # effective_text 缓存
    _all_materials: list[dict]  # 全量材料（用于 reviewer 上下文）
    _citation_whitelist: list[str]  # Writer 校验过的可引用章节名单（供 reviewer 核对）


# ── 图节点 ──────────────────────────────────────────────────────────


def _emit(coordinator, event: dict) -> None:
    """向调用方推送进度/流式事件（未设置 event_cb 时零开销）"""
    cb = getattr(coordinator, "_event_cb", None)
    if cb:
        try:
            cb(event)
        except Exception:
            pass  # 事件推送（如 SSE 断开）不应影响主流程


def _node_detect_intent(state: CoordinatorState, coordinator) -> dict:
    """节点: 意图识别（调用方已预算时跳过，避免重复 LLM 调用）"""
    if state.get("intent"):
        logger.info(f"  [Coordinator] 意图识别: {state['intent']}（预计算）")
        return {}
    query = state["query"]
    prompt = INTENT_PROMPT.format(query=query)
    response = call_llm([{"role": "user", "content": prompt}])
    intent = (response or "").strip().lower()
    if intent not in ["character", "relationship", "summary", "complex", "other"]:
        intent = "other"
    logger.info(f"  [Coordinator] 意图识别: {intent}")
    _emit(coordinator, {"event": "progress", "message": f"意图识别: {intent}"})
    return {"intent": intent}


def _node_decompose(state: CoordinatorState, coordinator) -> dict:
    """节点: 任务分解（调用方已预算时跳过，避免重复 LLM 调用）"""
    if state.get("steps"):
        logger.info(f"  [Coordinator] 任务拆解: {len(state['steps'])} 步（预计算）")
        return {}
    query = state["query"]
    intent = state["intent"]
    prompt = DECOMPOSE_PROMPT.format(intent=intent, query=query)
    response = call_llm([{"role": "user", "content": prompt}])
    steps = _extract_json_array(response)
    if not steps:
        steps = coordinator._default_steps(intent, query)
    logger.info(f"  [Coordinator] 任务拆解: {len(steps)} 步")
    # 重置步骤编号确保唯一
    for i, s in enumerate(steps):
        if "step" not in s:
            s["step"] = i + 1
    _emit(coordinator, {"event": "progress", "message": f"任务拆解: {len(steps)} 个检索步骤"})
    return {"steps": steps, "round_num": 0, "completed_steps": [],
            "_all_materials": [], "done": False}


def _node_research(state: CoordinatorState, coordinator) -> dict:
    """节点: 执行未完成的检索步骤（多步骤并行）"""
    steps = state["steps"]
    completed = state["completed_steps"]
    all_materials = state.get("_all_materials", [])
    completed_ids = {s["step"] for s in completed}

    pending = [s for s in steps if s["step"] not in completed_ids]

    def _run(step):
        desc = step["description"]
        logger.info(f"  [Coordinator] 分配任务: {desc}")
        _emit(coordinator, {"event": "progress", "message": f"检索中: {desc[:40]}"})
        try:
            result = coordinator.researcher.execute(desc, state["query"], state["intent"])
        except Exception as e:
            # 单步失败不拖垮整轮——其余步骤的材料照常汇总
            logger.warning(f"  [Coordinator] 检索步骤失败（继续其余步骤）: {desc}: {e}")
            result = f"检索步骤执行失败: {e}"
        return {"step": step["step"], "description": desc, "result": result}

    if len(pending) <= 1:
        round_materials = [_run(s) for s in pending]
    else:
        # 检索步骤之间无依赖，且 LLM 实体抽取 / 向量 / 图谱检索都是 IO 密集，
        # 并行后整轮耗时 ≈ 最慢的一步而非各步之和
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as pool:
            round_materials = list(pool.map(_run, pending))

    completed.extend(pending)
    all_materials.extend(round_materials)
    coordinator._pool.add_round(round_materials)

    return {
        "completed_steps": completed,
        "_all_materials": all_materials,
        "_materials_text": coordinator._pool.get_effective(),
    }


def _node_write(state: CoordinatorState, coordinator) -> dict:
    """节点: 生成报告（有 event_cb 时流式推送草稿 token）"""
    effective = state.get("_materials_text") or coordinator._pool.get_effective()
    materials = [{"step": 0, "description": "汇总资料", "result": effective}]
    _emit(coordinator, {"event": "progress", "message": "撰写报告中..."})

    # 流式回调仅当 Writer 实现支持时传入（兼容测试中注入的简化替身）
    import inspect
    write_kwargs = {}
    if "stream_cb" in inspect.signature(coordinator.writer.write).parameters \
            and getattr(coordinator, "_event_cb", None):
        _emit(coordinator, {"event": "draft_start"})
        write_kwargs["stream_cb"] = \
            lambda tok: _emit(coordinator, {"event": "token", "text": tok})

    draft = coordinator.writer.write(
        state["query"], state["intent"], materials, **write_kwargs)
    # 用与 Writer 内部校验完全相同的逻辑提取来源名单，交给 Reviewer 核对引用，
    # 避免 Reviewer 只能看到截断材料而误判 fake_citation。
    # _extract_chapter_sources 是 Writer 的静态方法，直接走类调用（兼容注入的替身）
    from core.agents.writer import Writer
    whitelist = Writer._extract_chapter_sources(materials)
    return {"draft": draft, "_citation_whitelist": whitelist}


def _node_review(state: CoordinatorState, coordinator) -> dict:
    """节点: 审核报告"""
    query = state["query"]
    draft = state["draft"]
    all_materials = state.get("_all_materials", [])

    sections = []
    whitelist = state.get("_citation_whitelist") or []
    if whitelist:
        sections.append(
            "【可引用章节白名单】（以下章节在检索材料中真实存在，"
            "报告引用名单内章节不属于编造引用）\n" + "\n".join(whitelist)
        )
    # 审核材料必须与 Writer 所见一致（effective_text：含图谱关系、PPR、社群等
    # 全部检索产物）。只给末几条截断材料会导致 Reviewer 把真实的图谱关系
    # 误判为幻觉——一次误判引发的重写轮成本远高于多给几千字上下文
    body = state.get("_materials_text") or coordinator._pool.get_effective()
    if not body.strip():
        body = "\n".join(
            [m.get("result", "")[:500] for m in all_materials[-3:] if m.get("result")]
        )
    if body.strip():
        sections.append("【检索到的原文片段】\n" + body)
    research_text = "\n\n".join(sections)[:8000]
    review = coordinator.reviewer.review(draft, query, research_materials=research_text)
    _emit(coordinator, {"event": "progress",
                        "message": f"审核: {review.get('score', 0)}/10 "
                                   f"{'通过' if review.get('passed') else '未通过，准备修订'}"})

    # 审计日志
    review_status = "failure" if not review["passed"] else "success"
    audit_log(
        action="review", user="system", resource=query[:120],
        detail=f"Round {state['round_num'] + 1}/{state['max_rounds']} | "
               f"Type: {review.get('failure_type', 'none')} | "
               f"Score: {review.get('score', 0)} | "
               f"Feedback: {review.get('feedback', '')[:200]}",
        status=review_status,
        failure_type=review.get("failure_type", ""),
    )

    if review["passed"]:
        logger.info(f"  [Coordinator] 审核通过（第 {state['round_num'] + 1} 轮）")
    else:
        logger.warning(f"  [Coordinator] 审核未通过 (类型: {review.get('failure_type', 'unknown')})")

    return {"review": review}


def _node_handle_failure(state: CoordinatorState, coordinator) -> dict:
    """节点: 审核失败 → 分类处理 → 生成补充步骤"""
    review = state["review"]
    steps = state["steps"]
    new_steps = coordinator._handle_review_failure(steps, review, state["query"])
    if new_steps:
        logger.info(f"  [Coordinator] 补充 {len(new_steps)} 个检索步骤后重试")
        next_step = max(s["step"] for s in steps) + 1
        for i, ns in enumerate(new_steps):
            ns["step"] = next_step + i
        steps = steps + new_steps
        return {"steps": steps, "round_num": state["round_num"] + 1}
    # 无法生成补充步骤 → 标记终止，避免空转重试（保持旧版 break 行为）
    logger.warning(f"  [Coordinator] 无法继续优化")
    return {"steps": steps, "round_num": state["round_num"] + 1, "done": True}


# ── 条件边 ──────────────────────────────────────────────────────────


def _route_after_review(state: CoordinatorState) -> str:
    """审核后路由"""
    review = state.get("review", {})
    if review.get("passed"):
        return "end"
    if state["round_num"] >= state["max_rounds"] - 1:
        logger.warning(f"  [Coordinator] 达到最大轮数，返回当前结果")
        return "end_maxed"
    return "handle_failure"


def _route_after_failure(state: CoordinatorState) -> str:
    """失败处理后路由：无补充步骤 → 终止；否则回到 research 重试"""
    if state.get("done"):
        return "end"
    return "research"


# ═════════════════════════════════════════════════════════════════════
#  协调员（保持旧接口兼容）
# ═════════════════════════════════════════════════════════════════════

class Coordinator:
    """协调员：LangGraph 驱动的多 Agent 编排"""

    def __init__(self, researcher, writer, reviewer):
        self.researcher = researcher
        self.writer = writer
        self.reviewer = reviewer
        self._pool: MaterialPool = None
        self._graph: CompiledStateGraph | None = None

    # ── 保持旧接口兼容 ──────────────────────────────────────────────

    def detect_intent(self, query):
        prompt = INTENT_PROMPT.format(query=query)
        response = call_llm([{"role": "user", "content": prompt}])
        intent = (response or "").strip().lower()
        if intent not in ["character", "relationship", "summary", "complex", "other"]:
            intent = "other"
        return intent

    def decompose_task(self, query, intent):
        prompt = DECOMPOSE_PROMPT.format(intent=intent, query=query)
        response = call_llm([{"role": "user", "content": prompt}])
        steps = _extract_json_array(response)
        if not steps:
            steps = self._default_steps(intent, query)
        return steps

    def _default_steps(self, intent, query):
        if intent == "character":
            return [
                {"step": 1, "description": f"在 Wiki 中搜索与「{query}」相关的人物信息"},
                {"step": 2, "description": f"在知识图谱中查找「{query}」的关系网络"},
            ]
        elif intent == "relationship":
            return [
                {"step": 1, "description": f"在知识图谱中查找「{query}」涉及的人物关系"},
                {"step": 2, "description": f"在 Wiki 中搜索相关章节的详细信息"},
            ]
        elif intent == "summary":
            return [
                {"step": 1, "description": "汇总所有 Wiki 章节的摘要信息"},
                {"step": 2, "description": "统计知识图谱中的人物和关系数据"},
            ]
        else:
            return [
                {"step": 1, "description": f"搜索与「{query}」相关的所有信息"},
            ]

    # ── 图构建（延迟初始化）──────────────────────────────────────────

    def _build_graph(self) -> CompiledStateGraph:
        """构建 LangGraph 状态图"""
        builder = StateGraph(CoordinatorState)

        # 注册节点（用 lambda 绑定 self）
        builder.add_node("detect_intent", lambda s: _node_detect_intent(s, self))
        builder.add_node("decompose", lambda s: _node_decompose(s, self))
        builder.add_node("research", lambda s: _node_research(s, self))
        builder.add_node("write", lambda s: _node_write(s, self))
        builder.add_node("review", lambda s: _node_review(s, self))
        builder.add_node("handle_failure", lambda s: _node_handle_failure(s, self))

        # 边
        builder.set_entry_point("detect_intent")
        builder.add_edge("detect_intent", "decompose")
        builder.add_edge("decompose", "research")
        builder.add_edge("research", "write")
        builder.add_edge("write", "review")

        # 条件边：审核通过 → END；不通过 → handle_failure → research（循环）
        builder.add_conditional_edges("review", _route_after_review, {
            "end": END,
            "end_maxed": END,
            "handle_failure": "handle_failure",
        })
        # 失败处理：有补充步骤 → research 重试；无法补充 → END（保持旧版 break 行为）
        builder.add_conditional_edges("handle_failure", _route_after_failure, {
            "end": END,
            "research": "research",
        })

        return builder.compile()

    # ── 主入口 ───────────────────────────────────────────────────────

    def run(self, query, max_rounds=5, intent=None, steps=None, event_cb=None):
        """
        完整流程：意图识别 → 任务分解 → 多轮执行 → 汇总输出

        参数:
            query: 用户问题
            max_rounds: 最多执行轮数
            intent: 可选，调用方预算好的意图（跳过 detect_intent 节点内的 LLM 调用）
            steps: 可选，调用方预算好的检索步骤（跳过 decompose 节点内的 LLM 调用）
            event_cb: 可选，事件回调 fn(dict)。图节点通过它推送
                progress / draft_start / token 等事件（SSE 流式用）

        返回:
            dict: {query, intent, steps, final_report, review_result, rounds}
        """
        logger.info(f"\n [Coordinator] 收到问题：{query}")

        self._pool = MaterialPool(llm_compress=True, max_rounds=3)
        self._event_cb = event_cb

        if self._graph is None:
            self._graph = self._build_graph()
        graph = self._graph

        if steps:
            # 与 _node_decompose 保持一致的编号规整
            for i, s in enumerate(steps):
                if "step" not in s:
                    s["step"] = i + 1

        initial_state: CoordinatorState = {
            "query": query,
            "max_rounds": max_rounds,
            "intent": intent or "",
            "steps": steps or [],
            "completed_steps": [],
            "round_num": 0,
            "draft": "",
            "review": {},
            "done": False,
            "_materials_text": "",
            "_all_materials": [],
            "_citation_whitelist": [],
        }

        # 执行图
        result = graph.invoke(initial_state)

        return {
            "query": query,
            "intent": result["intent"],
            "steps": result["steps"],
            "materials": result.get("_all_materials", []),
            "final_report": result["draft"],
            "review_result": result.get("review", {}),
            "rounds": result["round_num"] + 1,
            # 图跑完仍未通过审核 → 调用方自行决定是否人工介入
            "needs_manual_review": result.get("review", {}).get("passed") is False,
        }

    # ── 审核失败处理（保持旧逻辑）─────────────────────────────────────

    def _refine_plan(self, old_steps, feedback):
        prompt = f"""已有检索步骤：{json.dumps(old_steps, ensure_ascii=False)}
审核反馈：{feedback}
根据反馈，还需要补充哪些检索步骤？
以 JSON 数组格式返回，如 [{{"step": 3, "description": "..."}}]
不需要补充则返回 []"""
        response = call_llm([{"role": "user", "content": prompt}])
        new_steps = _extract_json_array(response)
        return new_steps if new_steps is not None else []

    def _handle_review_failure(self, old_steps, review, query=""):
        failure_type = review.get("failure_type", "other")
        feedback = review.get("feedback", "")
        retrieval_hints = review.get("retrieval_hints", [])
        missing_entities = review.get("missing_entities", [])

        new_steps = []
        next_step = max(s["step"] for s in old_steps) + 1 if old_steps else 1

        if failure_type == "evidence_missing" and (retrieval_hints or missing_entities):
            hints = retrieval_hints[:]
            if missing_entities:
                hints.extend([f"搜索「{e}」的详细信息" for e in missing_entities])
            for i, hint in enumerate(hints):
                new_steps.append({"step": next_step + i, "description": f"补充检索: {hint}"})
            logger.info(f"  [Coordinator] ↳ 证据不足 → 补充 {len(new_steps)} 个定向检索")

        elif failure_type == "alias_conflict" and missing_entities:
            for i, entity in enumerate(missing_entities):
                new_steps.append({"step": next_step + i,
                                  "description": f"核实关系: 在知识图谱中查询「{entity}」的所有关系"})
            logger.info(f"  [Coordinator] ↳ 实体冲突 → 核实 {len(new_steps)} 个实体关系")

        elif failure_type == "hallucination":
            hint = feedback[:200] if feedback else "检查报告是否有原文不存在的内容"
            new_steps.append({"step": next_step,
                              "description": f"原文验证: {hint}，请基于原文核实每条结论"})
            logger.info(f"  [Coordinator] ↳ 检测到幻觉 → 启动原文事实验证")

        elif failure_type == "fake_citation":
            logger.info(f"  [Coordinator] ↳ 编造引用 → 重新生成报告")
            hint = feedback[:200] if feedback else "报告中存在编造的引用"
            new_steps.append({"step": next_step,
                              "description": f"重新撰写: {hint}，只引用可引用的来源"})

        elif failure_type == "irrelevant":
            # 确定性补救：围绕原始问题全量重检 + 扣题重写，
            # 不再落入无引导的 _refine_plan 兜底
            new_steps.append({"step": next_step,
                              "description": f"围绕原始问题做全量综合检索: {query[:80]}"})
            new_steps.append({"step": next_step + 1,
                              "description": f"扣题重写: 逐段对照问题「{query[:50]}」，删除无关内容"})
            logger.info(f"  [Coordinator] ↳ 偏离问题 → 全量重检 + 扣题重写")

        else:
            logger.info(f"  [Coordinator] ↳ 其他问题 → 通用优化")

        if not new_steps:
            new_steps = self._refine_plan(old_steps, feedback)

        return new_steps
