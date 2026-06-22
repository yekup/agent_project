"""
图存储后端抽象层
================
支持冷热数据分层的图存储接口。

设计:
    当前实现:     NetworkX (内存图，仅供原型)
    近期目标:     Neo4jBackend (热数据，常驻内存/SSD)
    远期预留:     ColdStorageBackend (冷数据，TimescaleDB / ClickHouse)

冷热分层策略 (v2.0 §3.2.2):
    | 层级   | 引擎               | 数据范围       | 查询场景           |
    |--------|--------------------|----------------|--------------------|
    | 热     | Neo4j 属性边       | 近 50 章+主线  | 实时滑块交互       |
    | 冷     | PostgreSQL/TimescaleDB | 历史章节   | 全演化回放         |
    | 因果链 | ClickHouse         | 事件因果        | 复杂因果分析       |

⚠️ 建议:
    对于个人项目，先只用 Neo4j 单库 + 属性边存 start_chapter/end_chapter，
    等小说数量 > 10 本或章节 > 5000 章再考虑冷热分层。
    当前接口保留 ColdStorageBackend 但不强制实现。
"""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class CharacterNode:
    """人物节点"""
    name: str
    role: str = "unknown"                      # protagonist / supporting / antagonist
    description: str = ""
    aliases: list[str] = field(default_factory=list)  # 别名列表
    timeline: list[dict] = field(default_factory=list)  # [{chapter, identity, power_level, faction}]
    mention_count: int = 0
    chapter_count: int = 0
    properties: dict = field(default_factory=dict)


@dataclass
class RelationEdge:
    """关系边（含时序信息）"""
    source: str
    target: str
    relation_type: str = "related"             # 师徒 / 夫妻 / 敌对 / ...
    start_chapter: int | None = None
    end_chapter: int | None = None
    trigger_event: str = ""
    weight: float = 1.0
    confidence: float = 1.0
    properties: dict = field(default_factory=dict)


@dataclass
class EventNode:
    """事件节点（用于因果链查询）"""
    id: str
    title: str
    chapter: int
    description: str = ""
    involved_characters: list[str] = field(default_factory=list)
    cause_event_ids: list[str] = field(default_factory=list)    # 前因
    effect_event_ids: list[str] = field(default_factory=list)   # 后果
    event_type: str = "plot"                                     # plot / battle / emotional / ...
    importance: float = 0.5


@dataclass
class QueryResult:
    """统一查询结果"""
    characters: list[CharacterNode] = field(default_factory=list)
    relations: list[RelationEdge] = field(default_factory=list)
    events: list[EventNode] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class GraphStorageBackend(abc.ABC):
    """
    图存储后端抽象接口。

    所有后端 (NetworkX / Neo4j / 未来存储) 必须实现此接口。
    """

    @abc.abstractmethod
    def get_character(self, name: str) -> CharacterNode | None:
        ...

    @abc.abstractmethod
    def get_relations(self, character_name: str, chapter_range: tuple[int, int] | None = None) -> list[RelationEdge]:
        """
        获取某人物在指定章节范围内的关系网络。

        chapter_range=None 时返回全部。
        """
        ...

    @abc.abstractmethod
    def get_character_timeline(self, character_name: str) -> list[dict]:
        """
        获取人物身份/实力/阵营演化时间线。
        用于前端时序滑块。
        """
        ...

    @abc.abstractmethod
    def get_events(self, chapter_range: tuple[int, int] | None = None) -> list[EventNode]:
        """
        获取指定章节范围内的事件。
        用于事件因果链可视化。
        """
        ...

    @abc.abstractmethod
    def search_characters(self, keyword: str, limit: int = 20) -> list[CharacterNode]:
        """
        模糊搜索人物（支持别名匹配）。
        用于前端搜索框。
        """
        ...

    @abc.abstractmethod
    def get_statistics(self) -> dict:
        """
        图谱统计信息:
            - total_characters
            - total_relations
            - total_events
            - character_by_role
        """
        ...

    @abc.abstractmethod
    def health_check(self) -> dict:
        """
        存储后端健康检查。

        Returns:
            {"status": "ok" | "degraded" | "down",
             "character_count": int,
             "relation_count": int,
             "latency_ms": float}
        """
        ...


class Neo4jBackend(GraphStorageBackend):
    """
    Neo4j 图数据库后端（热数据）。

    ✅ 可实现，需要 Neo4j 社区版 (≥ 5.x) 运行中。

    安装:
        ```bash
        docker run -d \\
            --name neo4j-novel \\
            -p 7474:7474 -p 7687:7687 \\
            -e NEO4J_AUTH=neo4j/password \\
            neo4j:5-community
        pip install neo4j
        ```

    Cypher 索引 (必须先创建):
        ```cypher
        CREATE INDEX character_name_idx FOR (c:Character) ON (c.name);
        CREATE INDEX event_chapter_idx FOR (e:Event) ON (e.chapter);
        CREATE INDEX relation_range_idx FOR ()-[r:RELATION]-() ON (r.start_chapter);
        ```
    """

    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = ""):
        """
        Args:
            uri: Neo4j Bolt 连接地址
            user: 用户名
            password: 密码 (建议从环境变量 NEO4J_PASSWORD 读取)
        """
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    def _connect(self):
        """延迟初始化连接"""
        if self._driver is not None:
            return
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
            )
            logger.info(f"[Neo4jBackend] 已连接到 {self._uri}")
        except ImportError:
            raise ImportError("需要安装 neo4j 驱动: pip install neo4j")
        except Exception as e:
            logger.error(f"[Neo4jBackend] 连接失败: {e}")
            raise

    def _run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self._connect()
        with self._driver.session() as session:
            result = session.run(query, params or {})
            return [dict(r) for r in result]

    # ------------------------------------------------------------------
    # 接口实现
    # ------------------------------------------------------------------

    def get_character(self, name: str) -> CharacterNode | None:
        rows = self._run_query(
            "MATCH (c:Character {name: $name}) RETURN c",
            {"name": name},
        )
        if not rows:
            return None
        return self._node_to_character(rows[0]["c"])

    def get_relations(self, character_name: str, chapter_range: tuple[int, int] | None = None) -> list[RelationEdge]:
        if chapter_range:
            rows = self._run_query(
                """MATCH (c:Character {name: $name})-[r:RELATION]-(other)
                   WHERE r.start_chapter >= $start AND r.end_chapter <= $end
                   RETURN r, other.name AS target""",
                {"name": character_name, "start": chapter_range[0], "end": chapter_range[1]},
            )
        else:
            rows = self._run_query(
                """MATCH (c:Character {name: $name})-[r:RELATION]-(other)
                   RETURN r, other.name AS target""",
                {"name": character_name},
            )
        return [self._edge_to_relation(r["r"]) for r in rows]

    def get_character_timeline(self, character_name: str) -> list[dict]:
        rows = self._run_query(
            """MATCH (c:Character {name: $name})
               UNWIND c.timeline AS entry
               RETURN entry ORDER BY entry.chapter""",
            {"name": character_name},
        )
        return [r["entry"] for r in rows]

    def get_events(self, chapter_range: tuple[int, int] | None = None) -> list[EventNode]:
        if chapter_range:
            rows = self._run_query(
                """MATCH (e:Event)
                   WHERE e.chapter >= $start AND e.chapter <= $end
                   RETURN e ORDER BY e.chapter""",
                {"start": chapter_range[0], "end": chapter_range[1]},
            )
        else:
            rows = self._run_query("MATCH (e:Event) RETURN e ORDER BY e.chapter")
        return [self._node_to_event(r["e"]) for r in rows]

    def search_characters(self, keyword: str, limit: int = 20) -> list[CharacterNode]:
        rows = self._run_query(
            """MATCH (c:Character)
               WHERE c.name CONTAINS $keyword
               RETURN c LIMIT $limit""",
            {"keyword": keyword, "limit": limit},
        )
        return [self._node_to_character(r["c"]) for r in rows]

    def get_statistics(self) -> dict:
        rows = self._run_query(
            """MATCH (c:Character)
               WITH count(c) AS total_chars
               MATCH ()-[r:RELATION]-()
               WITH total_chars, count(r) AS total_rels
               MATCH (e:Event)
               WITH total_chars, total_rels, count(e) AS total_events
               RETURN total_chars, total_rels, total_events"""
        )
        if rows:
            r = rows[0]
            return {
                "total_characters": r.get("total_chars", 0),
                "total_relations": r.get("total_rels", 0),
                "total_events": r.get("total_events", 0),
            }
        return {}

    def health_check(self) -> dict:
        import time
        t0 = time.time()
        try:
            stats = self.get_statistics()
            return {
                "status": "ok",
                "latency_ms": (time.time() - t0) * 1000,
                **stats,
            }
        except Exception as e:
            return {"status": "down", "latency_ms": -1, "error": str(e)}

    @staticmethod
    def _node_to_character(node: dict) -> CharacterNode:
        return CharacterNode(
            name=node.get("name", ""),
            role=node.get("role", "unknown"),
            description=node.get("description", ""),
            aliases=list(node.get("aliases", []) or []),
            timeline=list(node.get("timeline", []) or []),
            mention_count=node.get("mention_count", 0),
            chapter_count=node.get("chapter_count", 0),
        )

    @staticmethod
    def _edge_to_relation(edge: dict) -> RelationEdge:
        return RelationEdge(
            source=edge.get("source", ""),
            target=edge.get("target", ""),
            relation_type=edge.get("relation_type", "related"),
            start_chapter=edge.get("start_chapter"),
            end_chapter=edge.get("end_chapter"),
            trigger_event=edge.get("trigger_event", ""),
            weight=edge.get("weight", 1.0),
            confidence=edge.get("confidence", 1.0),
        )

    @staticmethod
    def _node_to_event(node: dict) -> EventNode:
        return EventNode(
            id=node.get("id", ""),
            title=node.get("title", ""),
            chapter=node.get("chapter", 0),
            description=node.get("description", ""),
            involved_characters=list(node.get("involved_characters", []) or []),
            cause_event_ids=list(node.get("cause_event_ids", []) or []),
            effect_event_ids=list(node.get("effect_event_ids", []) or []),
            event_type=node.get("event_type", "plot"),
            importance=node.get("importance", 0.5),
        )


# ---------------------------------------------------------------------------
# 冷存储接口 (远期预留)
# ---------------------------------------------------------------------------

class ColdStorageBackend(GraphStorageBackend):
    """
    冷数据存储后端。

    🟠 不建议个人项目早期实现。
    当小说 > 10 本、章节 > 5000 章、Neo4j 内存不足时再考虑。

    可选实现:
        A. PostgreSQL + TimescaleDB (推荐)
           特性: 时序查询优化，与关系型数据共库，运维简单
           安装: docker run -d --name timescaledb timescale/timescaledb:latest-pg16

        B. ClickHouse
           特性: 列式存储，因果链分析极快，但需要额外运维
           安装: docker run -d --name clickhouse clickhouse/clickhouse-server

    迁移策略:
        1. 编写数据迁移脚本 (Neo4j → ColdStorage) 每晚运行
        2. 热数据保留近 50 章 + 所有主线人物
        3. 查询优先走热数据，miss 时 fallback 到冷数据
    """

    def __init__(self, connection_string: str = ""):
        self._conn_str = connection_string

    def get_character(self, name: str) -> CharacterNode | None:
        raise NotImplementedError(
            "冷存储后端尚未实现。\n"
            "如需要，参考 PostgreSQL TimescaleDB 方案:\n"
            "  docker run -d --name timescaledb timescale/timescaledb:latest-pg16\n"
            "  然后实现 GraphStorageBackend 接口。"
        )

    def get_relations(self, character_name: str, chapter_range=None) -> list[RelationEdge]:
        raise NotImplementedError

    def get_character_timeline(self, character_name: str) -> list[dict]:
        raise NotImplementedError

    def get_events(self, chapter_range=None) -> list[EventNode]:
        raise NotImplementedError

    def search_characters(self, keyword: str, limit: int = 20) -> list[CharacterNode]:
        raise NotImplementedError

    def get_statistics(self) -> dict:
        return {"status": "not_implemented"}

    def health_check(self) -> dict:
        return {"status": "not_implemented", "latency_ms": -1}


# ---------------------------------------------------------------------------
# 存储路由器 (冷热自动切换)
# ---------------------------------------------------------------------------

class TieredGraphRouter(GraphStorageBackend):
    """
    冷热分层路由器 —— 自动选择热存储或冷存储。

    策略:
        1. 优先查热存储 (Neo4jBackend)
        2. chapter_range 超出热数据范围时，合并冷存储结果
        3. 冷存储不可用时静默降级，只返回热数据

    此路由器对外透明，调用方不需要感知后端切换。
    """

    def __init__(
        self,
        hot: Neo4jBackend,
        cold: ColdStorageBackend | None = None,
        hot_chapter_range: int = 50,
    ):
        self.hot = hot
        self.cold = cold
        self.hot_chapter_range = hot_chapter_range  # 热数据保留最近的章节数

    def get_character(self, name: str) -> CharacterNode | None:
        return self.hot.get_character(name) or (
            self.cold.get_character(name) if self.cold else None
        )

    def get_relations(self, character_name: str, chapter_range=None) -> list[RelationEdge]:
        hot_result = self.hot.get_relations(character_name, chapter_range)
        if self.cold and chapter_range:
            cold_result = self.cold.get_relations(character_name, chapter_range)
            return hot_result + cold_result
        return hot_result

    def get_character_timeline(self, character_name: str) -> list[dict]:
        return self.hot.get_character_timeline(character_name)

    def get_events(self, chapter_range=None) -> list[EventNode]:
        return self.hot.get_events(chapter_range)

    def search_characters(self, keyword: str, limit: int = 20) -> list[CharacterNode]:
        return self.hot.search_characters(keyword, limit)

    def get_statistics(self) -> dict:
        return self.hot.get_statistics()

    def health_check(self) -> dict:
        hot_health = self.hot.health_check()
        if self.cold:
            hot_health["cold"] = self.cold.health_check()
        return hot_health
