"""
Novel-GraphRAG MCP Server
=========================
暴露网文知识图谱和 Wiki 数据给 MCP 客户端（Cursor、Claude Desktop 等）。

工具列表:
    - search_novel_graph: 搜索人物和关系网络
    - get_character_timeline: 追踪人物在全书中的演化
    - analyze_chapter: 获取单章的结构化分析
    - list_novels: 列出可用的书籍
    - search_wiki: 全文搜索 Wiki 条目

启动:
    # 方式一：独立运行
    python mcp_server.py

    # 方式二：通过 MCP 客户端配置
    # Claude Desktop settings.json:
    "mcpServers": {
        "novel-graphrag": {
            "command": "python",
            "args": ["path/to/mcp_server.py"]
        }
    }
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ── 路径配置 ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
WIKI_DIR = DATA_DIR / "wiki"
PROCESSED_DIR = DATA_DIR / "processed"

# 默认书籍（绍宋数据最完整）
DEFAULT_NOVEL = "shaosong"
DEFAULT_NOVEL_ZH = "绍宋"


# ── 数据加载器 ─────────────────────────────────────────────────────────

class NovelData:
    """加载并缓存一部小说的所有数据"""

    def __init__(self, novel_key: str):
        self.key = novel_key
        self._wiki: dict | None = None
        self._graph: dict | None = None
        self._novel_json: dict | None = None
        self._name: str = novel_key

    # ── 属性 ──────────────────────────────────────────────────────

    @property
    def wiki(self) -> dict:
        if self._wiki is None:
            paths = [
                WIKI_DIR / f"{self.key}_hierarchical.json",
                WIKI_DIR / f"{self.key}_wiki.json",
            ]
            for p in paths:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict) and "chapters" in raw:
                        self._wiki = raw
                    elif isinstance(raw, list):
                        self._wiki = {
                            "book": {"type": "book", "title": self.key, "summary": ""},
                            "volumes": [],
                            "chapters": raw,
                        }
                    break
            if self._wiki is None:
                self._wiki = {"book": {}, "volumes": [], "chapters": []}
        return self._wiki

    @property
    def graph(self) -> dict:
        if self._graph is None:
            path = WIKI_DIR / f"{self.key}_graph.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self._graph = json.load(f)
            else:
                self._graph = {"nodes": [], "edges": []}
        return self._graph

    @property
    def chapters(self) -> list[dict]:
        """获取章节 Wiki 条目列表"""
        return self.wiki.get("chapters", [])

    @property
    def volumes(self) -> list[dict]:
        return self.wiki.get("volumes", [])

    @property
    def book_summary(self) -> dict:
        return self.wiki.get("book", {})

    @property
    def nodes(self) -> list[dict]:
        return self.graph.get("nodes", [])

    @property
    def edges(self) -> list[dict]:
        return self.graph.get("edges", [])

    @property
    def display_name(self) -> str:
        """返回可读的小说名"""
        cn_map = {
            "shaosong": "《绍宋》",
            "斗破苍穹": "《斗破苍穹》",
            "神印王座": "《神印王座》",
        }
        return cn_map.get(self.key, self.key)

    # ── 检索方法 ──────────────────────────────────────────────────

    def search_characters(self, keyword: str, limit: int = 20) -> list[dict]:
        """模糊搜索人物"""
        kw = keyword.lower().strip()
        if not kw:
            return self.nodes[:limit]

        results = []
        for n in self.nodes:
            name = n.get("name", "")
            name_clean = name.split("（")[0].split("(")[0].lower()
            if kw in name.lower() or kw in name_clean:
                score = 10 if name.startswith(keyword) else 5
                results.append((score, n))
            elif kw in n.get("role", "").lower():
                results.append((3, n))

        results.sort(key=lambda x: -x[0])
        return [n for _, n in results[:limit]]

    def get_character_relations(self, character_name: str) -> list[dict]:
        """获取某人物所有关系"""
        rels = []
        for e in self.edges:
            if e["source"] == character_name:
                rels.append({
                    "character": e["target"],
                    "direction": "out",
                    "relation": e["relation"],
                    "weight": e.get("weight", 1),
                    "chapters": e.get("chapters", []),
                })
            elif e["target"] == character_name:
                rels.append({
                    "character": e["source"],
                    "direction": "in",
                    "relation": e["relation"],
                    "weight": e.get("weight", 1),
                    "chapters": e.get("chapters", []),
                })
        rels.sort(key=lambda r: -r["weight"])
        return rels

    def get_character_appearances(self, character_name: str) -> list[dict]:
        """追踪某人物在哪些章节出场及其摘要"""
        appearances = []
        for ch in self.chapters:
            for c in ch.get("characters", []):
                if c["name"] == character_name or (
                    character_name in c["name"]
                ):
                    appearances.append({
                        "chapter_index": ch.get("chapter_index", 0),
                        "chapter_title": ch.get("chapter_title", ""),
                        "summary": ch.get("summary", ""),
                        "description": c.get("description", ""),
                        "events": ch.get("events", []),
                    })
                    break
            # 也检查 main_characters
            if character_name in ch.get("main_characters", []):
                # 可能在 volumes 层有更详细的描述
                pass
        return appearances

    def get_chapter(self, identifier: str | int) -> dict | None:
        """按标题或索引查找章节"""
        if isinstance(identifier, int) or identifier.isdigit():
            idx = int(identifier)
            if 0 <= idx < len(self.chapters):
                return self.chapters[idx]
            return None

        # 尝试匹配标题
        for ch in self.chapters:
            title = ch.get("chapter_title", "")
            if identifier.lower() in title.lower():
                return ch
        return None

    def get_volume_by_chapter(self, chapter_index: int) -> dict | None:
        """找到包含某章节的卷"""
        for vol in self.volumes:
            cr = vol.get("chapter_range", [0, 0])
            if cr[0] <= chapter_index + 1 <= cr[1]:
                return vol
        return None

    def search_wiki_entries(self, query: str, top_k: int = 5) -> list[dict]:
        """全文搜索 Wiki（匹配标题/摘要/人物/事件）"""
        q = query.lower()
        scored = []
        for ch in self.chapters:
            score = 0
            title = (ch.get("chapter_title") or "").lower()
            summary = (ch.get("summary") or "").lower()

            if q in title:
                score += 10
            if q in summary:
                score += 5
            for c in ch.get("characters", []):
                if q in c["name"].lower():
                    score += 8
            for e in ch.get("events", []):
                if q in e.lower():
                    score += 6

            if score > 0:
                scored.append((score, ch))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]

    def character_summary(self, character_name: str) -> dict:
        """生成单个人物的完整画像"""
        node = None
        for n in self.nodes:
            if n["name"] == character_name:
                node = n
                break

        appearances = self.get_character_appearances(character_name)
        relations = self.get_character_relations(character_name)

        # 提取外貌/事件线索
        descriptions = []
        key_events = []
        for a in appearances:
            if a.get("description"):
                descriptions.append({
                    "chapter": a["chapter_title"],
                    "description": a["description"],
                })
            for ev in a.get("events", []):
                if ev not in key_events:
                    key_events.append(ev)

        return {
            "name": character_name,
            "role": node.get("role", "") if node else "",
            "mention_count": node.get("mention_count", 0) if node else 0,
            "chapter_count": node.get("chapter_count", 0) if node else 0,
            "appearances_count": len(appearances),
            "relation_count": len(relations),
            "key_relations": relations[:15],
            "key_events": key_events[:10],
            "descriptions": descriptions[:5],
        }


# ── 数据注册表 ─────────────────────────────────────────────────────────

_NOVEL_CACHE: dict[str, NovelData] = {}


def get_novel(key: str) -> NovelData:
    """获取（或创建）小说数据实例"""
    if key not in _NOVEL_CACHE:
        _NOVEL_CACHE[key] = NovelData(key)
    return _NOVEL_CACHE[key]


def list_available_novels() -> list[dict]:
    """列出所有已编译图谱的小说"""
    novels = []
    for f in WIKI_DIR.glob("*_graph.json"):
        name = f.stem.replace("_graph", "")
        if name in ("test",):
            continue
        wiki_path = WIKI_DIR / f"{name}_hierarchical.json"
        cn_map = {
            "shaosong": "《绍宋》",
            "斗破苍穹": "《斗破苍穹》",
            "神印王座": "《神印王座》",
        }
        novels.append({
            "key": name,
            "name": cn_map.get(name, name),
            "has_wiki": wiki_path.exists(),
        })
    return novels


# ── MCP Server ─────────────────────────────────────────────────────────

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types


server = Server("novel-graphrag")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_novels",
            description="列出所有已编译图谱的小说",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="search_novel_graph",
            description=(
                "搜索小说中的人物和关系网络。"
                "支持按人物名模糊搜索，返回人物档案、关系网络和出场章节。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "novel": {
                        "type": "string",
                        "description": "小说 key（默认 shaosong，可用 list_novels 查看）",
                        "default": DEFAULT_NOVEL,
                    },
                    "character": {
                        "type": "string",
                        "description": "要搜索的人物名（支持模糊匹配）",
                    },
                    "include_relations": {
                        "type": "boolean",
                        "description": "是否返回关系网络",
                        "default": True,
                    },
                },
                "required": ["character"],
            },
        ),
        types.Tool(
            name="get_character_timeline",
            description=(
                "追踪一个人物在全书中的人生轨迹。"
                "返回该人物出场的每一章及其描述，"
                "以及经历的关键事件列表。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "novel": {
                        "type": "string",
                        "description": "小说 key（默认 shaosong）",
                        "default": DEFAULT_NOVEL,
                    },
                    "character": {
                        "type": "string",
                        "description": "人物名（精确或模糊匹配）",
                    },
                },
                "required": ["character"],
            },
        ),
        types.Tool(
            name="analyze_chapter",
            description=(
                "分析某一章的内容。"
                "返回该章的摘要、出场人物、事件和关系变化。"
                "支持按章节号（如 '1' 或 '42'）或章节标题搜索。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "novel": {
                        "type": "string",
                        "description": "小说 key（默认 shaosong）",
                        "default": DEFAULT_NOVEL,
                    },
                    "chapter": {
                        "type": "string",
                        "description": "章节号（如 '1'）或章节标题关键词",
                    },
                },
                "required": ["chapter"],
            },
        ),
        types.Tool(
            name="search_wiki",
            description=(
                "全文搜索小说的 Wiki 条目。"
                "可搜索人物、事件、章节内容，返回匹配的章节摘要。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "novel": {
                        "type": "string",
                        "description": "小说 key（默认 shaosong）",
                        "default": DEFAULT_NOVEL,
                    },
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（支持人物名、事件、地点等）",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    novel_key = arguments.get("novel", DEFAULT_NOVEL)
    data = get_novel(novel_key)

    if name == "list_novels":
        novels = list_available_novels()
        if not novels:
            return [types.TextContent(
                type="text",
                text="# 📚 可用的书籍\n\n当前没有已编译的小说数据。请先运行编译管道。",
            )]
        lines = ["# 📚 已编译的小说\n"]
        for n in novels:
            status = "✅ Wiki 就绪" if n["has_wiki"] else "⚠️ 仅有图谱"
            lines.append(f"- **{n['name']}** (`{n['key']}`) — {status}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── search_novel_graph ────────────────────────────────────────
    if name == "search_novel_graph":
        character = arguments.get("character", "")
        include_relations = arguments.get("include_relations", True)

        # 搜索人物
        matches = data.search_characters(character, limit=10)
        if not matches:
            return [types.TextContent(
                type="text",
                text=f"未找到与「{character}」匹配的人物。\n"
                     f"提示: 可用 `search_wiki` 搜索相关章节，或尝试其他关键词。",
            )]

        lines = [
            f"# 🕸️ 人物搜索结果: 「{character}」",
            f"小说: {data.display_name}",
            f"匹配 {len(matches)} 个人物\n",
        ]

        for n in matches[:5]:
            name = n["name"]
            role = n.get("role", "")
            mention = n.get("mention_count", 0)
            lines.append(f"## {name} ({role})")
            lines.append(f"- 出场次数: {mention} 次")
            lines.append(f"- 相关章节: {n.get('chapter_count', 0)} 章")

            if include_relations:
                rels = data.get_character_relations(name)
                if rels:
                    lines.append(f"- 关系网络 ({len(rels)} 条):")
                    for r in rels[:8]:
                        if r["direction"] == "out":
                            lines.append(f"  → {r['character']}: {r['relation']}")
                        else:
                            lines.append(f"  ← {r['character']}: {r['relation']}")
                    if len(rels) > 8:
                        lines.append(f"  ...及另外 {len(rels) - 8} 条关系")
                else:
                    lines.append("- 关系网络: 暂无数据")
            lines.append("")

        # 附上 top 5 关系的总览
        if include_relations and matches:
            top_char = matches[0]["name"]
            all_rels = data.get_character_relations(top_char)
            if all_rels:
                lines.append(f"### {top_char} 的核心关系")
                for r in all_rels[:5]:
                    partner = r["character"]
                    # 查对方的角色
                    partner_node = None
                    for n in data.nodes:
                        if n["name"] == partner:
                            partner_node = n
                            break
                    role_tag = f"({partner_node['role']})" if partner_node else ""
                    lines.append(
                        f"- **{partner}** {role_tag}: {r['relation']}"
                        f" (权重 {r['weight']})"
                    )

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── get_character_timeline ─────────────────────────────────────
    if name == "get_character_timeline":
        character = arguments.get("character", "")
        # 先精确查找
        appearances = data.get_character_appearances(character)
        if not appearances:
            # 模糊搜索
            matches = data.search_characters(character, limit=1)
            if matches:
                character = matches[0]["name"]
                appearances = data.get_character_appearances(character)

        if not appearances:
            return [types.TextContent(
                type="text",
                text=f"未找到「{character}」的出场记录。",
            )]

        summary = data.character_summary(character)
        lines = [
            f"# ⏳ 人物时间线: {character}",
            f"小说: {data.display_name}",
            f"角色: {summary['role']} | 出场 {summary['appearances_count']} 章 | "
            f"提及 {summary['mention_count']} 次\n",
        ]

        if summary["key_events"]:
            lines.append("## 关键事件")
            for ev in summary["key_events"]:
                lines.append(f"- {ev}")
            lines.append("")

        lines.append("## 出场章节")
        for a in appearances:
            idx = a["chapter_index"]
            title = a["chapter_title"]
            # 找到所属的卷
            vol = data.get_volume_by_chapter(idx)
            vol_tag = f"[{vol['title']}]" if vol else ""
            lines.append(f"### {'第' + str(a['chapter_index']) + '章' if a.get('chapter_index') else ''} {title} {vol_tag}")
            lines.append(f"{a['summary'][:200]}")
            if a.get("description"):
                lines.append(f"> 人物表现: {a['description']}")
            lines.append("")

        # 在开头附加统计
        stats_lines = [
            f"# ⏳ 人物时间线: {character}",
            f"小说: {data.display_name}",
            f"角色: {summary['role']} | 出场 {summary['appearances_count']} 章 | "
            f"提及 {summary['mention_count']} 次 | "
            f"关系 {summary['relation_count']} 条\n",
        ]
        if summary["key_events"]:
            stats_lines.append("## 关键事件")
            for ev in summary["key_events"]:
                stats_lines.append(f"- {ev}")
            stats_lines.append("")

        lines = stats_lines + lines[7:]  # 替换开头

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── analyze_chapter ────────────────────────────────────────────
    if name == "analyze_chapter":
        chapter_id = arguments.get("chapter", "")

        ch = data.get_chapter(chapter_id)
        if ch is None:
            return [types.TextContent(
                type="text",
                text=f"未找到章节: 「{chapter_id}」。\n"
                     f"提示: 可使用章节号（数字）或标题关键词。"
                     f"共有 {len(data.chapters)} 章",
            )]

        title = ch.get("chapter_title", "")
        idx = ch.get("chapter_index", 0)
        vol = data.get_volume_by_chapter(idx)

        lines = [
            f"# 📖 章节分析: {title}",
            f"小说: {data.display_name}",
            f"章节索引: {idx}",
            f"所属卷: {vol['title'] if vol else '无'}\n",
        ]

        lines.append("## 摘要")
        lines.append(ch.get("summary", "无摘要"))
        lines.append("")

        chars = ch.get("characters", [])
        if chars:
            lines.append(f"## 出场人物 ({len(chars)}人)")
            for c in chars:
                lines.append(f"- **{c['name']}** ({c.get('role', '')}): {c.get('description', '')}")
            lines.append("")

        events = ch.get("events", [])
        if events:
            lines.append("## 关键事件")
            for i, ev in enumerate(events, 1):
                lines.append(f"{i}. {ev}")
            lines.append("")

        rels = ch.get("relationships", [])
        if rels:
            lines.append("## 关系变化")
            for r in rels:
                lines.append(f"- {r['source']} ↔ {r['target']}: {r['relation']}")
            lines.append("")

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── search_wiki ────────────────────────────────────────────────
    if name == "search_wiki":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)

        results = data.search_wiki_entries(query, top_k=top_k)
        if not results:
            return [types.TextContent(
                type="text",
                text=f"未找到与「{query}」相关的 Wiki 条目。",
            )]

        lines = [
            f"# 🔍 Wiki 搜索结果: 「{query}」",
            f"小说: {data.display_name}",
            f"匹配 {len(results)} 条\n",
        ]
        for r in results:
            title = r.get("chapter_title", "")
            summary = r.get("summary", "")[:200]
            chars = "、".join(
                c["name"] for c in r.get("characters", [])[:5]
            )
            lines.append(f"### {title}")
            lines.append(f"{summary}...")
            if chars:
                lines.append(f"人物: {chars}")
            lines.append("")

        return [types.TextContent(type="text", text="\n".join(lines))]

    raise ValueError(f"未知工具: {name}")


# ── 入口 ───────────────────────────────────────────────────────────────

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="novel-graphrag",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
