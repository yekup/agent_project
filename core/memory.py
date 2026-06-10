"""
Session 记忆系统
每轮对话结束自动压缩摘要，跨对话时检索相关旧对话
"""
import json
import os
import time
from datetime import datetime


class SessionMemory:
    """会话记忆管理器"""

    def __init__(self, memory_dir="data/memory"):
        self.memory_dir = memory_dir
        self.index_path = os.path.join(memory_dir, "index.json")
        os.makedirs(memory_dir, exist_ok=True)
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

    def new_session(self, query):
        """新建会话，返回 session_id"""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        entry = {
            "session_id": session_id,
            "title": query[:30],
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
