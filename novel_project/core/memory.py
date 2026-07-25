"""
Session 记忆系统
每轮对话结束自动压缩摘要，跨对话时检索相关旧对话
"""
import json
import os
import time
from datetime import datetime


# ── 实体提取工具 ──────────────────────────────────────────────────────────

def extract_entities_from_graph(query: str, report: str, novel: str) -> list[str]:
    """
    从活动图谱节点名中做子串匹配提取实体，不调 LLM。

    加载 data/wiki/{novel}_graph.json 的节点 name 字段，
    在 query + report 合并文本中做子串匹配，
    返回匹配到的节点名列表（按出场次数降序排列）。
    """
    graph_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "wiki", f"{novel}_graph.json",
    )
    if not os.path.exists(graph_path):
        return []

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            graph_data = json.load(f)
    except Exception:
        return []

    text = (query + " " + report).lower()
    nodes = graph_data.get("nodes", [])

    scored = []
    for n in nodes:
        name = n.get("name", "")
        if name and len(name) >= 2 and name in text:
            scored.append((n.get("mention_count", 0), name))

    scored.sort(key=lambda x: -x[0])
    return [name for _, name in scored]


class SessionMemory:
    """会话记忆管理器"""

    def __init__(self, memory_dir="data/memory"):
        self.memory_dir = memory_dir
        self.index_path = os.path.join(memory_dir, "index.json")
        os.makedirs(memory_dir, exist_ok=True)
        os.makedirs(os.path.join(memory_dir, "sessions"), exist_ok=True)
        self._load_index()

    def _load_index(self):
        """加载记忆索引"""
        if os.path.exists(self.index_path):
            with open(self.index_path, "r", encoding="utf-8") as f:
                self.index = json.load(f)
        else:
            self.index = []

    def _save_index(self):
        """保存记忆索引"""
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

    def new_session(self, query, novel=""):
        """新建会话，返回 session_id"""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        entry = {
            "session_id": session_id,
            "title": query[:30],
            "novel": novel,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": "",
            "key_entities": [],
            "message_count": 0,
            "is_active": True,
        }
        self.index.append(entry)
        self._save_index()
        return session_id

    def save_summary(self, session_id, summary, entities):
        """保存会话摘要"""
        for entry in self.index:
            if entry["session_id"] == session_id:
                entry["summary"] = summary
                entry["key_entities"] = entities
                entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry["message_count"] += 1
                break
        self._save_index()

    def end_session(self, session_id):
        """结束会话"""
        for entry in self.index:
            if entry["session_id"] == session_id:
                entry["is_active"] = False
                break
        self._save_index()

    def search(self, query, top_k=3):
        """根据当前问题检索相关旧会话"""
        query_lower = query.lower()
        scored = []
        for entry in self.index:
            if entry.get("is_active", False):
                continue
            score = 0
            for entity in entry.get("key_entities", []):
                if entity in query_lower:
                    score += 5
            if entry.get("summary"):
                for word in query_lower.split():
                    if len(word) > 1 and word in entry["summary"].lower():
                        score += 2
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]

    def append_turn(self, session_id, query, report, novel, review_passed, entities):
        """
        追加一条对话轮次记录到会话文件（完整原料保留）。

        存储路径: data/memory/sessions/{session_id}.json
        文件结构: {session_id, novel, review_passed,
                   turns: [{query, report, ts}], entities, created_at}
        使用原子写入（临时文件 + os.replace）。
        """
        import tempfile

        sessions_dir = os.path.join(self.memory_dir, "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        filepath = os.path.join(sessions_dir, f"{session_id}.json")

        # 加载或初始化会话文件
        record = None
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    record = json.load(f)
            except Exception:
                record = None

        if record is None:
            record = {
                "session_id": session_id,
                "novel": novel,
                "review_passed": review_passed,
                "turns": [],
                "entities": entities,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            # 累积更新
            record["review_passed"] = review_passed
            # 实体合并去重
            existing = set(record.get("entities", []))
            for e in entities:
                existing.add(e)
            record["entities"] = list(existing)

        turn = {
            "query": query,
            "report": report,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        record["turns"].append(turn)

        # 更新索引中的 entities
        for entry in self.index:
            if entry["session_id"] == session_id:
                existing = set(entry.get("key_entities", []))
                for e in entities:
                    existing.add(e)
                entry["key_entities"] = list(existing)
                entry["review_passed"] = review_passed
                break
        self._save_index()

        # 原子写入
        fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=sessions_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, filepath)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
