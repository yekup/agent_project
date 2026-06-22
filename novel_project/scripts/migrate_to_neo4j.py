#!/usr/bin/env python3
"""
NetworkX → Neo4j 迁移脚本
===========================
将现有的 JSON 图谱文件导入 Neo4j 图数据库。

导入内容:
    - Character 节点 (人物)
    - RELATION 边 (关系，含时序属性)
    - Event 节点 (事件，用于因果链查询)

用法:
    # 1. 先确保 Neo4j 运行中
    docker run -d --name neo4j-novel \\
        -p 7474:7474 -p 7687:7687 \\
        -e NEO4J_AUTH=neo4j/password \\
        neo4j:5-community

    # 2. 导入
    python scripts/migrate_to_neo4j.py --novel shaosong

    # 3. 验证
    python scripts/migrate_to_neo4j.py --verify --novel shaosong

数据模型:
    (c:Character {
        name: str,
        role: str,
        mention_count: int,
        chapter_count: int,
        aliases: [str],
        timeline: [{chapter, identity, ...}]
    })

    (c1)-[r:RELATION {
        type: str,           # 关系类型 (师徒/夫妻/敌对)
        start_chapter: int,  # 关系开始的章节
        end_chapter: int,    # 关系结束的章节
        trigger_event: str,  # 触发事件
        weight: int,         # 权重
        chapters: [str]      # 涉及的章节列表
    }]->(c2)

    (e:Event {
        id: str,
        title: str,
        chapter: int,
        description: str,
        event_type: str,
        importance: float
    })
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 添加项目路径
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_to_neo4j")


# ── 常量 ────────────────────────────────────────────────────────────────

NEO4J_DEFAULT_URI = "bolt://localhost:7687"
NEO4J_DEFAULT_USER = "neo4j"
NEO4J_DEFAULT_PASSWORD = os.environ.get("NEO4J_PASSWORD", "novelgraphrag2024")


# ── Cypher 语句生成 ────────────────────────────────────────────────────

class CypherGenerator:
    """
    Cypher 语句生成器。

    两种用法:
        1. generate_cypher_script() → 输出 .cypher 文件，可直接在 Neo4j Browser 运行
        2. import_via_driver() → 用 Python driver 实时导入
    """

    # 约束与索引
    CONSTRAINTS = """
    CREATE CONSTRAINT character_name_unique IF NOT EXISTS
    FOR (c:Character) REQUIRE c.name IS UNIQUE;

    CREATE INDEX event_chapter_idx IF NOT EXISTS
    FOR (e:Event) ON (e.chapter);

    CREATE INDEX relation_chapter_idx IF NOT EXISTS
    FOR ()-[r:RELATION]-() ON (r.start_chapter);
    """

    def __init__(self, novel_key: str):
        self.novel_key = novel_key
        self.graph_path = BASE_DIR / f"data/wiki/{novel_key}_graph.json"

    def generate_cypher_script(self, output_path: str | None = None) -> str:
        """
        生成完整的 Cypher 导入脚本。

        输出格式:
            // 约束
            // 人物节点
            // 关系边
            // 事件节点
        """
        with open(self.graph_path, "r", encoding="utf-8") as f:
            graph = json.load(f)

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        lines = [
            f"// Neo4j 导入脚本 — {self.novel_key}",
            f"// 生成时间: {datetime.now().isoformat()}",
            f"// 人物: {len(nodes)}, 关系: {len(edges)}",
            "",
        ]

        # 约束与索引
        lines.append("// === 约束与索引 ===")
        lines.append(self.CONSTRAINTS)
        lines.append("")

        # 人物节点
        lines.append("// === 人物节点 ===")
        for n in nodes:
            name = n.get("name", "")
            role = n.get("role", "unknown")
            mention = n.get("mention_count", 0)
            chapter_count = n.get("chapter_count", 0)
            # 用属性安全转义
            name_esc = name.replace("'", "\\'")
            lines.append(
                f"MERGE (c:Character {{name: '{name_esc}'}})\n"
                f"  ON CREATE SET c.role = '{role}', "
                f"c.mention_count = {mention}, "
                f"c.chapter_count = {chapter_count}, "
                f"c.novel = '{self.novel_key}';"
            )
        lines.append("")

        # 关系边
        lines.append("// === 关系边 ===")
        for i, e in enumerate(edges):
            source = e.get("source", "").replace("'", "\\'")
            target = e.get("target", "").replace("'", "\\'")
            relation = e.get("relation", "").replace("'", "\\'")
            weight = e.get("weight", 1)
            chapters = e.get("chapters", [])

            # 提取起始章节
            start_ch = self._extract_chapter_num(chapters[0]) if chapters else 0
            end_ch = self._extract_chapter_num(chapters[-1]) if chapters else 0

            # 简化关系类型（从关系描述中提取）
            rel_type = self._simplify_relation(relation)

            ch_list = "', '".join(c.replace("'", "\\'") for c in chapters)

            lines.append(
                f"MATCH (a:Character {{name: '{source}'}}), "
                f"(b:Character {{name: '{target}'}})\n"
                f"MERGE (a)-[r:RELATION {{type: '{rel_type}'}}]->(b)\n"
                f"  SET r.start_chapter = {start_ch}, "
                f"r.end_chapter = {end_ch}, "
                f"r.weight = {weight}, "
                f"r.chapters = ['{ch_list}'], "
                f"r.novel = '{self.novel_key}';"
            )

        # 批量提交
        lines.insert(3, "\nBEGIN\n")
        lines.append("\nCOMMIT\n")

        script = "\n".join(lines)

        if output_path:
            output_path = str(BASE_DIR / output_path)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(script)
            logger.info(f"Cypher 脚本已生成: {output_path} ({len(nodes)} 节点, {len(edges)} 边)")

        return script

    def create_event_nodes(self, wiki_path: str | None = None) -> list[dict]:
        """
        从 Wiki 数据生成 Event 节点。

        从章节的 events 字段提取关键事件，
        跨章自动合并相同事件。
        """
        if wiki_path is None:
            wiki_path = BASE_DIR / f"data/wiki/{self.novel_key}_hierarchical.json"

        if not os.path.exists(wiki_path):
            logger.warning(f"Wiki 文件不存在: {wiki_path}，跳过事件节点")
            return []

        with open(wiki_path, "r", encoding="utf-8") as f:
            wiki = json.load(f)

        chapters = wiki.get("chapters", [])
        events: list[dict] = []
        seen_events: set[str] = set()

        for ch in chapters:
            chapter_title = ch.get("chapter_title", "")
            chapter_index = ch.get("chapter_index", 0)
            for ev_text in ch.get("events", []):
                # 去重
                ev_key = ev_text[:30]
                if ev_key not in seen_events:
                    seen_events.add(ev_key)
                    events.append({
                        "id": f"ev_{self.novel_key}_{len(events)}",
                        "title": ev_text[:80],
                        "chapter": chapter_index + 1,
                        "description": ev_text,
                        "event_type": self._classify_event(ev_text),
                        "importance": 0.5,
                    })

        # 保存事件节点为 JSON
        event_path = BASE_DIR / f"data/wiki/{self.novel_key}_events.json"
        with open(event_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        logger.info(f"事件节点已保存: {event_path} ({len(events)} 个事件)")

        return events

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _extract_chapter_num(chapter_title: str) -> int:
        """从章节标题提取数字 ('第三章' → 3, '第42章' → 42)"""
        cn_nums = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        # 尝试阿拉伯数字
        m = re.search(r"(\d+)", chapter_title)
        if m:
            return int(m.group(1))
        # 尝试中文数字
        m = re.search(r"第([一二三四五六七八九十]+)", chapter_title)
        if m:
            num = 0
            for c in m.group(1):
                num = num * 10 + cn_nums.get(c, 0)
            return num
        return 0

    @staticmethod
    def _simplify_relation(relation_text: str) -> str:
        """从关系描述中提取简化的关系类型"""
        relation_text = relation_text.lower()
        if "师徒" in relation_text or "师" in relation_text:
            return "师徒"
        if "夫妻" in relation_text or "夫" in relation_text or "妻" in relation_text:
            return "夫妻"
        if "兄弟" in relation_text or "姐妹" in relation_text:
            return "手足"
        if "朋友" in relation_text or "好友" in relation_text:
            return "朋友"
        if "敌人" in relation_text or "敌对" in relation_text or "仇" in relation_text:
            return "敌对"
        if "君臣" in relation_text:
            return "君臣"
        if "父子" in relation_text or "母子" in relation_text or "父女" in relation_text or "母女" in relation_text:
            return "亲子"
        return "其他"

    @staticmethod
    def _classify_event(event_text: str) -> str:
        """分类事件类型"""
        event_text = event_text.lower()
        if any(w in event_text for w in ["战", "杀", "攻", "守", "败", "胜", "围"]):
            return "战斗"
        if any(w in event_text for w in ["封", "任", "升", "贬", "罢"]):
            return "人事"
        if any(w in event_text for w in ["婚", "嫁", "娶"]):
            return "情感"
        if any(w in event_text for w in ["谋", "计", "策", "议"]):
            return "谋略"
        if any(w in event_text for w in ["诏", "旨", "令", "政"]):
            return "政令"
        return "情节"


# ── Neo4j 导入器 ───────────────────────────────────────────────────────

class Neo4jImporter:
    """
    通过 Python Driver 实时导入 Neo4j。

    需要安装 neo4j 驱动:
        pip install neo4j

    用法:
        importer = Neo4jImporter()
        importer.connect(password="your-password")
        importer.import_graph("shaosong")
        importer.close()
    """

    def __init__(
        self,
        uri: str = NEO4J_DEFAULT_URI,
        user: str = NEO4J_DEFAULT_USER,
    ):
        self.uri = uri
        self.user = user
        self.driver = None

    def connect(self, password: str = NEO4J_DEFAULT_PASSWORD) -> bool:
        """连接 Neo4j"""
        try:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, password))
            # 验证连接
            with self.driver.session() as session:
                result = session.run("RETURN 1 AS test")
                record = result.single()
                assert record and record["test"] == 1
            logger.info(f"Neo4j 连接成功: {self.uri}")
            return True
        except ImportError:
            logger.error("需要安装 neo4j 驱动: pip install neo4j")
            return False
        except Exception as e:
            logger.error(f"Neo4j 连接失败: {e}")
            logger.info("提示: 确保 Neo4j 正在运行")
            logger.info("  docker run -d --name neo4j-novel -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5-community")
            return False

    def import_graph(self, novel_key: str) -> dict:
        """导入一部小说的图谱"""
        if not self.driver:
            logger.error("未连接到 Neo4j，请先调用 connect()")
            return {"status": "error", "message": "未连接"}

        cypher = CypherGenerator(novel_key)
        graph_path = BASE_DIR / f"data/wiki/{novel_key}_graph.json"

        if not graph_path.exists():
            return {"status": "error", "message": f"图谱文件不存在: {graph_path}"}

        with open(graph_path, "r", encoding="utf-8") as f:
            graph = json.load(f)

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        stats = {"nodes_created": 0, "edges_created": 0, "errors": []}

        # 创建约束
        with self.driver.session() as session:
            session.run("CREATE CONSTRAINT character_name_unique IF NOT EXISTS FOR (c:Character) REQUIRE c.name IS UNIQUE;")

        # 批量导入节点
        with self.driver.session() as session:
            for n in nodes:
                try:
                    session.run(
                        """MERGE (c:Character {name: $name})
                           ON CREATE SET
                               c.role = $role,
                               c.mention_count = $mention_count,
                               c.chapter_count = $chapter_count,
                               c.novel = $novel
                           ON MATCH SET
                               c.mention_count = $mention_count""",
                        name=n["name"],
                        role=n.get("role", "unknown"),
                        mention_count=n.get("mention_count", 0),
                        chapter_count=n.get("chapter_count", 0),
                        novel=novel_key,
                    )
                    stats["nodes_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"节点 {n['name']}: {e}")

            logger.info(f"人物节点导入: {stats['nodes_created']}/{len(nodes)}")

        # 批量导入关系
        with self.driver.session() as session:
            for e in edges:
                try:
                    # 提取关系类型
                    rel_type = CypherGenerator._simplify_relation(e.get("relation", ""))
                    chapters = e.get("chapters", [])
                    start_ch = CypherGenerator._extract_chapter_num(chapters[0]) if chapters else 0
                    end_ch = CypherGenerator._extract_chapter_num(chapters[-1]) if chapters else 0

                    session.run(
                        """MATCH (a:Character {name: $source})
                           MATCH (b:Character {name: $target})
                           MERGE (a)-[r:RELATION {type: $type}]->(b)
                           SET r.start_chapter = $start_ch,
                               r.end_chapter = $end_ch,
                               r.weight = $weight,
                               r.novel = $novel""",
                        source=e["source"],
                        target=e["target"],
                        type=rel_type,
                        start_ch=start_ch,
                        end_ch=end_ch,
                        weight=e.get("weight", 1),
                        novel=novel_key,
                    )
                    stats["edges_created"] += 1
                except Exception as ex:
                    stats["errors"].append(f"关系 {e.get('source')}-{e.get('target')}: {ex}")

            logger.info(f"关系边导入: {stats['edges_created']}/{len(edges)}")

        # 导入事件节点
        try:
            events = cypher.create_event_nodes()
            with self.driver.session() as session:
                for ev in events:
                    session.run(
                        """MERGE (e:Event {id: $id})
                           SET e.title = $title,
                               e.chapter = $chapter,
                               e.description = $description,
                               e.event_type = $event_type,
                               e.importance = $importance,
                               e.novel = $novel""",
                        **ev, novel=novel_key,
                    )
            logger.info(f"事件节点导入: {len(events)}")
            stats["events_created"] = len(events)
        except Exception as ex:
            logger.warning(f"事件导入跳过: {ex}")

        if stats["errors"]:
            logger.warning(f"导入中共 {len(stats['errors'])} 个错误")

        return stats

    def verify(self, novel_key: str) -> dict:
        """验证 Neo4j 中的图谱数据"""
        if not self.driver:
            return {"status": "not_connected"}

        with self.driver.session() as session:
            char_count = session.run("MATCH (c:Character {novel: $novel}) RETURN count(c) AS cnt", novel=novel_key).single()["cnt"]
            rel_count = session.run("MATCH ()-[r:RELATION {novel: $novel}]-() RETURN count(r) AS cnt", novel=novel_key).single()["cnt"]
            event_count = session.run("MATCH (e:Event {novel: $novel}) RETURN count(e) AS cnt", novel=novel_key).single()["cnt"]
            sample = session.run(
                "MATCH (c:Character {novel: $novel}) RETURN c.name, c.role ORDER BY c.mention_count DESC LIMIT 5",
                novel=novel_key,
            ).data()

        return {
            "novel": novel_key,
            "characters": char_count,
            "relations": rel_count,
            "events": event_count,
            "top_characters": [r["c.name"] for r in sample],
            "timestamp": datetime.now().isoformat(),
            "verified": char_count > 0,
        }

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("Neo4j 连接已关闭")


# ── 导出 GEXF (Graph Exchange XML Format) ──────────────────────────────

def export_gexf(novel_key: str, output_path: str | None = None) -> str:
    """
    将图谱导出为 GEXF 格式，可在 Gephi 中可视化。

    Gephi 是免费的图可视化工具: https://gephi.org/
    """
    graph_path = BASE_DIR / f"data/wiki/{novel_key}_graph.json"
    if not graph_path.exists():
        logger.error(f"图谱文件不存在: {graph_path}")
        return ""

    with open(graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gexf xmlns="http://www.gexf.net/1.3" version="1.3">',
        "  <graph mode=\"static\" defaultedgetype=\"undirected\">",
    ]

    # 节点
    lines.append("    <nodes>")
    nodes_list = graph.get("nodes", [])
    for i, n in enumerate(nodes_list):
        lines.extend([
            f'      <node id="{i}" label="{n["name"]}">',
            f'        <attvalues>',
            f'          <attvalue for="role" value="{n.get("role", "")}"/>',
            f'          <attvalue for="mention" value="{n.get("mention_count", 0)}"/>',
            f'        </attvalues>',
            f'      </node>',
        ])
    lines.append("    </nodes>")

    # 边
    lines.append("    <edges>")
    for i, e in enumerate(graph.get("edges", [])):
        src_idx = next((j for j, n in enumerate(nodes_list) if n["name"] == e["source"]), -1)
        tgt_idx = next((j for j, n in enumerate(nodes_list) if n["name"] == e["target"]), -1)
        if src_idx >= 0 and tgt_idx >= 0:
            lines.append(
                f'      <edge id="{i}" source="{src_idx}" target="{tgt_idx}" '
                f'weight="{e.get("weight", 1)}"/>'
            )
    lines.append("    </edges>")

    lines.extend(["  </graph>", "</gexf>"])

    xml = "\n".join(lines)

    if output_path:
        output_path = str(BASE_DIR / output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml)
        logger.info(f"GEXF 已导出: {output_path}")

    return xml


# ── 命令行入口 ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NetworkX → Neo4j 迁移工具")
    parser.add_argument("--novel", default="shaosong", help="小说 key")
    parser.add_argument("--verify", action="store_true", help="验证 Neo4j 数据")
    parser.add_argument("--export-cypher", action="store_true", help="导出 Cypher 脚本")
    parser.add_argument("--export-gexf", action="store_true", help="导出 GEXF 格式")
    parser.add_argument("--output", default="", help="输出路径")
    parser.add_argument("--import", dest="do_import", action="store_true", help="导入到 Neo4j")
    parser.add_argument("--password", default="", help="Neo4j 密码")
    args = parser.parse_args()

    cypher = CypherGenerator(args.novel)

    if args.export_cypher:
        output = args.output or f"data/wiki/{args.novel}_import.cypher"
        cypher.generate_cypher_script(output)
        print(f"Cypher 脚本已生成: {output}")
        print("在 Neo4j Browser 中打开或使用 cypher-shell:")
        print(f"  cat {output} | cypher-shell -u neo4j -p your-password")

    if args.export_gexf:
        output = args.output or f"data/wiki/{args.novel}.gexf"
        export_gexf(args.novel, output)
        print(f"GEXF 已导出: {output}")

    if args.verify or args.do_import:
        importer = Neo4jImporter()
        password = args.password or NEO4J_DEFAULT_PASSWORD
        if not importer.connect(password):
            print("\n❌ 无法连接到 Neo4j。请先启动 Neo4j:")
            print("   docker run -d --name neo4j-novel -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5-community")
            sys.exit(1)

        if args.do_import:
            print(f"导入 {args.novel} 到 Neo4j...")
            stats = importer.import_graph(args.novel)
            print(json.dumps(stats, ensure_ascii=False, indent=2))

        if args.verify:
            stats = importer.verify(args.novel)
            print(f"\n验证结果: {json.dumps(stats, ensure_ascii=False, indent=2)}")
            if stats.get("verified"):
                print("\n✅ Neo4j 图谱验证通过")
            else:
                print("\n❌ 图谱数据为空")

        importer.close()

    if not any([args.verify, args.do_import, args.export_cypher, args.export_gexf]):
        parser.print_help()
        print("\n---")
        print("没有指定操作。只生成事件节点:")
        events = cypher.create_event_nodes()
        print(f"  从 Wiki 中提取了 {len(events)} 个事件")


if __name__ == "__main__":
    main()
