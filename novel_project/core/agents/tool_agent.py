"""
工具驱动的自主 Agent 框架
=========================
替换硬编码的 Coordinator→Researcher→Writer→Reviewer 管道。

设计:
  1. AgentTool — 每个 Agent 能力抽象为一个工具，有 name/description/parameters/execute
  2. ToolRegistry — 注册和发现工具
  3. AutonomousCoordinator — 使用 LLM 函数调用自主编排工具执行
  4. 每次 LLM 调用最多尝试 N 步工具调用 → 最终生成回答

LLM 工具定义（OpenAI Function Calling 格式）:
  {
    "type": "function",
    "function": {
      "name": "search_wiki",
      "description": "在小说Wiki中检索信息",
      "parameters": {"type": "object", "properties": {"query": {...}}, "required": ["query"]}
    }
  }

用法:
    coordinator = AutonomousCoordinator(tools=[WikiTool, GraphTool, WriterTool, ReviewTool])
    result = await coordinator.run("赵玖是什么角色？")  # 异步
"""
from __future__ import annotations

import abc
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.llm import call_llm
from core.material_pool import MaterialPool

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
#  工具抽象
# ═════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    """工具执行结果"""
    tool_name: str
    success: bool
    content: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)


class AgentTool(abc.ABC):
    """
    一个 Agent 工具。

    每个工具对应 LLM 的一个可用能力。
    子类只需实现 execute() 和 name/description 等元数据。
    """

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    # 是否允许 LLM 在没有用户确认时自动调用此工具
    auto_call: bool = True

    def to_openai_tool(self) -> dict:
        """转为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": list(self.parameters.keys()),
                },
            },
        }

    @abc.abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """执行工具，传入命名参数，返回结构化结果"""
        ...


# ═════════════════════════════════════════════════════════════════════
#  具体工具实现
# ═════════════════════════════════════════════════════════════════════

class SearchWikiTool(AgentTool):
    """检索小说 Wiki 中的信息"""

    name = "search_wiki"
    description = ("在小说 Wiki（章节摘要、人物信息、卷摘要）中检索与查询相关的内容。"
                   "适合查找人物、事件、章节概要。")
    parameters = {
        "query": {"type": "string", "description": "搜索关键词"},
        "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
    }

    def __init__(self, retriever):
        self.retriever = retriever

    def execute(self, query: str, top_k: int = 5) -> ToolResult:
        try:
            results = self.retriever.search_wiki(query, top_k=top_k)
            if not results:
                return ToolResult(self.name, True, "Wiki 中未找到相关信息。")
            lines = []
            for r in results:
                title = r.get("chapter_title") or r.get("title", "")
                summary = (r.get("summary") or "")[:200]
                if title:
                    lines.append(f"【{title}】{summary}")
            return ToolResult(self.name, True, "\n".join(lines),
                              {"count": len(results), "chapters": [r.get("chapter_title", "") for r in results]})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))


class SearchGraphTool(AgentTool):
    """检索知识图谱中的关系"""

    name = "search_graph"
    description = ("在人物关系图谱中检索与查询相关的节点和关系。"
                   "适合查找人物关系、角色关联。")
    parameters = {
        "query": {"type": "string", "description": "查询关键词（通常是人物名）"},
    }

    def __init__(self, retriever):
        self.retriever = retriever

    def execute(self, query: str) -> ToolResult:
        try:
            result = self.retriever.search_by_graph(query)
            nodes = result.get("matched_nodes", [])
            relations = result.get("relations", [])
            if not nodes and not relations:
                return ToolResult(self.name, True, "知识图谱中未找到相关信息。")
            lines = []
            if nodes:
                lines.append(f"匹配人物：{'、'.join(nodes[:10])}")
            for r in relations[:10]:
                lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")
            return ToolResult(self.name, True, "\n".join(lines),
                              {"nodes": nodes, "relation_count": len(relations)})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))


class SearchVectorTool(AgentTool):
    """向量语义检索原文"""

    name = "search_vector"
    description = ("在原文段落的向量语义索引中检索与查询最相关的内容。"
                   "适合找不到精确关键词时使用，例如主题性、概括性问题。")
    parameters = {
        "query": {"type": "string", "description": "语义搜索查询"},
        "top_k": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
    }

    def __init__(self, retriever):
        self.retriever = retriever

    def execute(self, query: str, top_k: int = 10) -> ToolResult:
        try:
            results = self.retriever.search_by_vector(query, top_k=top_k)
            if not results or "未就绪" in results[0].get("text", ""):
                return ToolResult(self.name, True, "向量库未就绪，跳过。")
            lines = []
            for r in results[:10]:
                text = r.get("text", "")[:300]
                meta = r.get("metadata", {})
                ch_title = meta.get("chapter_title", "")
                lines.append(f"📄 [{ch_title}] {text}")
            return ToolResult(self.name, True, "\n".join(lines),
                              {"count": len(results)})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))


class GenerateReportTool(AgentTool):
    """生成分析报告"""

    name = "generate_report"
    description = ("基于收集到的资料生成分析报告。在收集完足够的检索材料后调用此工具。"
                   "不要在资料不足时调用——先调用 search_wiki / search_graph 收集材料。")
    parameters = {
        "query": {"type": "string", "description": "原始用户问题"},
        "intent": {"type": "string", "description": "分析类型: character/relationship/summary/complex"},
    }

    def __init__(self, writer, material_pool: MaterialPool):
        self.writer = writer
        self.pool = material_pool

    def execute(self, query: str, intent: str = "other") -> ToolResult:
        try:
            materials = [{
                "step": 0, "description": "汇总资料",
                "result": self.pool.get_effective(),
            }]
            report = self.writer.write(query, intent, materials)
            return ToolResult(self.name, True, report, {"length": len(report)})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))


class ReviewReportTool(AgentTool):
    """质量审核报告"""

    name = "review_report"
    description = ("审核生成的分析报告质量。检查是否有幻觉、引用是否真实、是否回答了问题。"
                   "当 generate_report 完成后应调用此工具确认质量。")
    parameters = {
        "report": {"type": "string", "description": "待审核的报告全文"},
        "query": {"type": "string", "description": "原始用户问题"},
    }

    def __init__(self, reviewer, all_materials: list[dict]):
        self.reviewer = reviewer
        self.all_materials = all_materials

    def execute(self, report: str, query: str) -> ToolResult:
        try:
            research_text = "\n".join(
                [m.get("result", "")[:500] for m in self.all_materials[-3:] if m.get("result")]
            )[:3000]
            result = self.reviewer.review(report, query, research_materials=research_text)
            return ToolResult(
                self.name, True,
                json.dumps({"passed": result["passed"], "score": result["score"],
                            "feedback": result.get("feedback", "")}, ensure_ascii=False),
                result,
            )
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))


# ═════════════════════════════════════════════════════════════════════
#  工具注册表
# ═════════════════════════════════════════════════════════════════════

class ToolRegistry:
    """工具注册表"""
    def __init__(self):
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[AgentTool]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        return [t.to_openai_tool() for t in self._tools.values()]


# ═════════════════════════════════════════════════════════════════════
#  自主协调器
# ═════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一个网文分析助手。你可以使用以下工具来分析小说并回答用户问题。

工作流程:
1. 理解用户问题后，先用 search_wiki 和 search_graph 检索相关信息
2. 如果语义搜索场景（需要找全文匹配），使用 search_vector
3. 收集到足够的资料后，用 generate_report 生成分析报告
4. 用 review_report 审核报告质量
5. 如果审核不通过，根据反馈补充检索后重新生成
6. 最终将分析结果展示给用户

注意:
- 每条结论必须基于检索到的原文，不要编造
- 如果检索材料不足以回答，如实告知用户
- 引用要标注来源"""


class AutonomousCoordinator:
    """
    自主协调器：使用 LLM 函数调用模式自主编排工具。
    """

    MAX_LLM_CALLS = 10  # 最多调用 LLM 10 次（每轮可含多个工具调用）

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._all_materials: list[dict] = []

    def run(self, query: str, max_rounds: int = 5) -> dict:
        """
        自主执行查询。

        循环:
          1. LLM 决定要调用哪些工具
          2. 执行工具 → 收集结果
          3. LLM 评估 → 继续或结束
          4. 达到最大步数或 LLM 决定结束时返回最终报告
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        pool = MaterialPool(llm_compress=True, max_rounds=3)
        final_report = ""
        round_num = 0
        llm_calls = 0
        tools = self.registry.to_openai_tools()

        while llm_calls < self.MAX_LLM_CALLS:
            llm_calls += 1
            round_num += 1

            logger.info(f"[AutoCoordinator] Round {round_num}, LLM call {llm_calls}")

            # LLM 调用（带工具列表）
            response = self._call_llm_with_tools(messages, tools)

            if response is None:
                break

            # 处理工具调用
            tool_calls = response.get("tool_calls", [])

            if not tool_calls:
                # LLM 直接返回了回答文本 → 结束
                final_report = response.get("content", "")
                if final_report:
                    break
                # 如果也没有纯文本 → 从新一轮开始
                continue

            # 执行工具
            round_materials = []
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                tool = self.registry.get(fn_name)
                if tool is None:
                    result = ToolResult(fn_name, False, error=f"未知工具: {fn_name}")
                else:
                    logger.info(f"  [AutoCoordinator] 调用: {fn_name}({fn_args})")
                    result = tool.execute(**fn_args)

                self._all_materials.append({
                    "round": round_num,
                    "tool": fn_name,
                    "result": result.content[:1000] if result.success else f"错误: {result.error}",
                })
                round_materials.append({
                    "step": round_num * 100 + len(round_materials),
                    "description": f"[{fn_name}]",
                    "result": result.content[:1000] if result.success else f"错误: {result.error}",
                })

                # 把工具结果追加为 LLM 消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{llm_calls}"),
                    "content": json.dumps({
                        "tool": fn_name,
                        "success": result.success,
                        "result": result.content[:1500],
                    }, ensure_ascii=False),
                })

            # 更新材料池（如果 generate_report 被调用了，需要交材料给它）
            if round_materials:
                pool.add_round(round_materials)

            # 检查是否已经生成报告
            has_report = any(tc.get("function", {}).get("name") == "generate_report"
                             for tc in tool_calls)
            has_review = any(tc.get("function", {}).get("name") == "review_report"
                             for tc in tool_calls)

            if has_report and has_review:
                # 检查审核是否通过
                for tc in tool_calls:
                    if tc.get("function", {}).get("name") == "review_report":
                        for m in self._all_materials[::-1]:
                            if m["tool"] == "review_report":
                                try:
                                    review_data = json.loads(m["result"])
                                    if review_data.get("passed"):
                                        logger.info("[AutoCoordinator] 审核通过，结束")
                                        break
                                except Exception:
                                    pass

            if round_num >= max_rounds * 2:
                break

        # 提取最终报告
        if not final_report:
            for m in reversed(self._all_materials):
                if m["tool"] == "generate_report":
                    final_report = m["result"]
                    break

        return {
            "query": query,
            "final_report": final_report or "未能生成报告",
            "rounds": round_num,
            "llm_calls": llm_calls,
            "materials": self._all_materials,
        }

    def _call_llm_with_tools(
        self, messages: list[dict], tools: list[dict]
    ) -> dict | None:
        """
        调用 LLM 并可能返回工具调用或纯文本。

        返回: {"content": str, "tool_calls": [...]} 或 None
        """
        import os
        from openai import OpenAI

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        model = os.environ.get("LLM_MODEL", "deepseek-chat")

        if not api_key:
            return None

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)

            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=4096,
            )

            choice = resp.choices[0]
            msg = choice.message

            result = {}
            if msg.content:
                result["content"] = msg.content

            if msg.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
                # 把 assistant 的消息也追加到历史
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": result["tool_calls"],
                })

            return result if result else None

        except Exception as e:
            logger.error(f"[AutoCoordinator] LLM 调用失败: {e}")
            return None
