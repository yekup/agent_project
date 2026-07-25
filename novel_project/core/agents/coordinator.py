"""
协调员 Agent
负责：意图识别 → 任务分解 → 分发任务 → 汇总结果
"""

import json
import logging
import os
import re
import sys
import uuid
import time

logger = logging.getLogger(__name__)

from core.llm import call_llm
from core.material_pool import MaterialPool
from core.security import audit_log


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

# 意图识别 prompt
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

# 任务分解 prompt
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

class Coordinator:
    """协调员：接收问题 → 意图识别 → 拆解任务 → 调度 Agent → 汇总"""

    def __init__(self, researcher, writer, reviewer):
        """
        参数:
            researcher: Researcher Agent 实例
            writer: Writer Agent 实例
            reviewer: Reviewer Agent 实例
        """
        self.researcher = researcher
        self.writer = writer
        self.reviewer = reviewer

    def detect_intent(self, query):
        """第 1 步：意图识别"""
        prompt = INTENT_PROMPT.format(query=query)
        response = call_llm([{"role": "user", "content": prompt}])
        intent = (response or "").strip().lower()
        if intent not in ["character", "relationship", "summary", "complex", "other"]:
            intent = "other"
        logger.info(f"  [Coordinator] 意图识别: {intent}")
        return intent

    def decompose_task(self, query, intent):
        """第 2 步：根据意图拆解任务"""
        prompt = DECOMPOSE_PROMPT.format(intent=intent, query=query)
        response = call_llm([{"role": "user", "content": prompt}])

        steps = _extract_json_array(response)
        if steps:
            logger.info(f"  [Coordinator] 任务拆解: {len(steps)} 步")
            return steps
        # 兜底：按意图类型走默认流程
        return self._default_steps(intent, query)

    def _default_steps(self, intent, query):
        """兜底任务分解"""
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

    def run(self,query,max_rounds=5):
        """
        完整流程：意图识别 → 任务拆解 → 多轮执行 → 汇总输出
        
        参数:
            query: 用户问题
            max_rounds: 最多执行轮数（防止死循环）
        
        返回:
            dict: {query, intent, steps, final_report, review_result}
        """
        logger.info(f"\n [Coordinator] 收到问题：{query}")

        #第一步：意图识别
        intent = self.detect_intent(query)

        #第二步：任务拆解
        steps = self.decompose_task(query,intent)

        #第三步：多轮执行（使用 MaterialPool 管理上下文膨胀）
        pool = MaterialPool(llm_compress=True, max_rounds=3)
        all_materials = []
        completed_steps = []

        for round_num in range(max_rounds):
            logger.info(f"\n  --- 第 {round_num + 1} 轮执行 ---")
            round_materials = []

            for step in steps:
                if step["step"] in [s["step"] for s in completed_steps]:
                    continue

                desc = step["description"]
                logger.info(f"  [Coordinator] 分配任务: {desc}")

                result = self.researcher.execute(desc, query, intent)
                round_materials.append({
                    "step": step["step"],
                    "description": desc,
                    "result": result,
                })
                completed_steps.append(step)

            all_materials.extend(round_materials)

            # 加入材料池（自动压缩旧材料）
            pool.add_round(round_materials)

            # 使用池中的有效文本生成报告（第1轮全量，后续轮次压缩）
            effective_text = pool.get_effective()
            draft = self.writer.write(query, intent, [{"step": 0, "description": "汇总资料", "result": effective_text}])

            # ── 审核（每轮都审，不再跳过第1轮）──
            # 将检索材料摘要传给 reviewer 做引用真实性验证
            research_text = "\n".join(
                [m.get("result", "")[:500] for m in all_materials[-3:] if m.get("result")]
            )[:3000]
            review = self.reviewer.review(draft, query, research_materials=research_text)

            # 记录审核审计日志
            review_status = "failure" if not review["passed"] else "success"
            failure_detail = (
                f"Round {round_num + 1}/{max_rounds} | "
                f"Type: {review.get('failure_type', 'none')} | "
                f"Score: {review.get('score', 0)} | "
                f"Feedback: {review.get('feedback', '')[:200]}"
            )
            audit_log(
                action="review",
                user="system",
                resource=query[:120],
                detail=failure_detail,
                status=review_status,
                failure_type=review.get("failure_type", ""),
            )

            if review["passed"]:
                logger.info(f"  [Coordinator] 审核通过（第 {round_num + 1} 轮）")
                return {
                    "query": query,
                    "intent": intent,
                    "steps": steps,
                    "materials": all_materials,
                    "final_report": draft,
                    "review_result": review,
                    "rounds": round_num + 1,
                }
            else:
                logger.warning(f"  [Coordinator] 审核未通过 (类型: {review.get('failure_type', 'unknown')})")
                logger.warning(f"  [Coordinator] 反馈: {review.get('feedback', '')[:120]}...")

                if round_num < max_rounds - 1:
                    new_steps = self._handle_review_failure(steps, review)
                    if new_steps:
                        logger.info(f"  [Coordinator] 补充 {len(new_steps)} 个检索步骤后重试")
                        steps.extend(new_steps)
                        continue  # 进入下一轮

                # 无法修复或已达最大轮数，返回当前结果
                logger.warning(f"  [Coordinator] 无法继续优化，返回当前结果（需人工审核）")
                break

        # 所有轮次用完仍未通过审核
        logger.warning(f"  [Coordinator] 审核未通过，返回最终结果（需人工审核）")
        return {
            "query": query,
            "intent": intent,
            "steps": completed_steps,
            "materials": all_materials,
            "final_report": draft,
            "review_result": review,
            "rounds": max_rounds,
            "needs_manual_review": True,
        }

    def _refine_plan(self, old_steps, feedback):
        """根据 Reviewer 反馈，补充新的检索步骤"""
        prompt = f"""已有检索步骤：{json.dumps(old_steps, ensure_ascii=False)}
审核反馈：{feedback}
根据反馈，还需要补充哪些检索步骤？
以 JSON 数组格式返回，如 [{{"step": 3, "description": "..."}}]
不需要补充则返回 []"""

        response = call_llm([{"role": "user", "content": prompt}])
        new_steps = _extract_json_array(response)
        if new_steps is not None:
            return new_steps
        return []

    def _handle_review_failure(self, old_steps, review):
        """
        根据审核失败类型生成针对性的补充检索步骤。

        分类处理:
          - evidence_missing → 使用 retrieval_hints 补充检索
          - alias_conflict   → 查询知识图谱核实实体关系
          - hallucination    → 基于原文事实验证
          - fake_citation    → 重新生成报告（注入可用来源列表）
          - irrelevant       → 重新分解任务
          - other            → 通用反馈兜底

        返回新步骤列表，无需补充返回 []。
        """
        failure_type = review.get("failure_type", "other")
        feedback = review.get("feedback", "")
        retrieval_hints = review.get("retrieval_hints", [])
        missing_entities = review.get("missing_entities", [])

        new_steps = []
        next_step = max(s["step"] for s in old_steps) + 1 if old_steps else 1

        if failure_type == "evidence_missing" and (retrieval_hints or missing_entities):
            # 证据不足 → 按 reviewers 提示定向补充检索
            hints = retrieval_hints[:]
            if missing_entities:
                hints.extend([f"搜索「{e}」的详细信息" for e in missing_entities])
            for i, hint in enumerate(hints):
                new_steps.append({
                    "step": next_step + i,
                    "description": f"补充检索: {hint}",
                })
            logger.info(f"  [Coordinator] ↳ 证据不足 → 补充 {len(new_steps)} 个定向检索")

        elif failure_type == "alias_conflict" and missing_entities:
            # 别名/实体冲突 → 知识图谱定向核实
            for i, entity in enumerate(missing_entities):
                new_steps.append({
                    "step": next_step + i,
                    "description": f"核实关系: 在知识图谱中查询「{entity}」的所有关系",
                })
            logger.info(f"  [Coordinator] ↳ 实体冲突 → 核实 {len(new_steps)} 个实体关系")

        elif failure_type == "hallucination":
            # 幻觉 → 基于原文事实验证
            hallucination_hint = feedback[:200] if feedback else "检查报告是否有原文不存在的内容"
            new_steps.append({
                "step": next_step,
                "description": f"原文验证: {hallucination_hint}，请基于原文（不是 Wiki 摘要）核实每条结论",
            })
            logger.info(f"  [Coordinator] ↳ 检测到幻觉 → 启动原文事实验证")

        elif failure_type == "fake_citation":
            # 编造引用 → 直接重新生成报告（不需要检索）
            # 在下一轮中 Writer 会注入可用来源列表并校验引用
            logger.info(f"  [Coordinator] ↳ 编造引用 → 重新生成报告")
            # 给 Writer 一个更严格的指令
            citation_hint = feedback[:200] if feedback else "报告中存在编造的章节引用，必须只引用检索到的实际章节"
            new_steps.append({
                "step": next_step,
                "description": f"重新撰写: {citation_hint}，只引用【可引用的来源章节】中的内容",
            })

        elif failure_type == "irrelevant":
            # 偏离问题 → 用通用反馈重新分解
            logger.info(f"  [Coordinator] ↳ 偏离问题 → 重新规划检索策略")

        else:
            # other / 兜底 → 通用反馈处理
            logger.info(f"  [Coordinator] ↳ 其他问题 → 通用优化")

        # 如果专项策略未生成步骤，用 _refine_plan 兜底
        if not new_steps:
            new_steps = self._refine_plan(old_steps, feedback)

        return new_steps
