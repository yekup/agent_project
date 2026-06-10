"""
协调员 Agent
负责：意图识别 → 任务分解 → 分发任务 → 汇总结果
"""

import json
import os
import sys
import uuid
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

from agent_project.core.llm import call_llm

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

注意：Researcher 只能执行以下三类检索：
1. 搜索 Wiki（章节摘要和人物信息）
2. 搜索知识图谱（人物关系）
3. 搜索原文（章节正文）
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
        intent = response.strip().lower()
        if intent not in ["character", "relationship", "summary", "complex", "other"]:
            intent = "other"
        print(f"  [Coordinator] 意图识别: {intent}")
        return intent

    def decompose_task(self, query, intent):
        """第 2 步：根据意图拆解任务"""
        prompt = DECOMPOSE_PROMPT.format(intent=intent, query=query)
        response = call_llm([{"role": "user", "content": prompt}])

        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                steps = json.loads(json_match.group())
                print(f"  [Coordinator] 任务拆解: {len(steps)} 步")
                return steps
            except json.JSONDecodeError:
                pass
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
        print(f"\n [Coordinator] 收到问题：{query}")

        #第一步：意图识别
        intent = self.detect_intent(query)

        #第二步：任务拆解
        steps = self.decompose_task(query,intent)

        #第三步：多轮执行
        all_materials = []
        completed_steps = []

        for round_num in range(max_rounds):
            print(f"\n  --- 第 {round_num + 1} 轮执行 ---")
            round_materials = []

            #执行未完成的步骤
            for step in steps:
                if step["step"] in [s["step"] for s in completed_steps]:
                    continue

                desc = step["description"]
                print(f"  [Coordinator] 分配任务: {desc}")

                result = self.researcher.execute(desc, query, intent)
                round_materials.append({
                    "step": step["step"],
                    "description": desc,
                    "result": result,
                })
                completed_steps.append(step)

            all_materials.extend(round_materials)

            # 收集本轮所有新资料
            new_info = "\n".join([m["result"] for m in round_materials if m["result"]])

            # 让 Writer 尝试生成报告
            draft = self.writer.write(query, intent, all_materials)

            # Reviewer 审核
            review = self.reviewer.review(draft, query)

            if review["passed"]:
                print(f"  [Coordinator] 审核通过（第 {round_num + 1} 轮）")
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
                print(f"  [Coordinator] 审核未通过: {review['feedback']}")
                if round_num < max_rounds - 1:
                    # 还有轮次，根据审核反馈补充检索
                    new_steps = self._refine_plan(steps, review["feedback"])
                    if new_steps:
                        steps.extend(new_steps)

        # 达到最大轮次，返回最后一版
        print(f"  [Coordinator] 达到最大轮次，返回当前版本")
        return {
            "query": query,
            "intent": intent,
            "steps": completed_steps,
            "materials": all_materials,
            "final_report": draft,
            "review_result": review,
            "rounds": max_rounds,
        }

    def _refine_plan(self, old_steps, feedback):
        """根据 Reviewer 反馈，补充新的检索步骤"""
        prompt = f"""已有检索步骤：{json.dumps(old_steps, ensure_ascii=False)}
审核反馈：{feedback}
根据反馈，还需要补充哪些检索步骤？
以 JSON 数组格式返回，如 [{{"step": 3, "description": "..."}}]
不需要补充则返回 []"""
        
        response = call_llm([{"role": "user", "content": prompt}])
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                new_steps = json.loads(json_match.group())
                return new_steps
            except json.JSONDecodeError:
                pass
        return []
