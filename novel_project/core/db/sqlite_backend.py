"""
SQLite 数据库后端
=================
线程安全、多进程安全（WAL 模式），替代 JSON 文件后端。

特点:
  - 使用 sqlite3 标准库，零额外依赖
  - WAL 模式支持多进程并发读写
  - 自动建表，迁移无需手动干预
  - 兼容 DBBackend 抽象接口，通过 set_db() 一键切换
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.db.interface import DBBackend
from core.db.models import UserModel, AuditLogModel

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "novel_graphrag.db")


class SQLiteBackend(DBBackend):
    """SQLite 后端（线程安全，WAL 模式）"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（线程安全）"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")     # 多进程安全
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        """建表 + 默认 admin 用户"""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                resource TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'success',
                ip TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
            CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_logs(username);
            CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_logs(status);
            CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS semantic_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                embedding TEXT,
                hit_count INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '',
                last_hit_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cache_hash ON semantic_cache(query_hash);
        """)
        conn.commit()

        # 创建默认 admin（bcrypt 密码，安全策略与 JSON 后端一致）
        existing = conn.execute("SELECT 1 FROM users WHERE username = 'admin'").fetchone()
        if not existing:
            import secrets
            import uuid
            from core.security import hash_password

            password = os.environ.get("ADMIN_PASSWORD", "").strip()
            if password:
                logger.info("[SQLiteBackend] 默认 admin 密码来自环境变量 ADMIN_PASSWORD")
            else:
                password = secrets.token_urlsafe(12)
                logger.warning("[SQLiteBackend] 默认 admin 随机密码: %s", password)

            pwd_hash = hash_password(password)
            username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"u_{uuid.uuid4().hex[:8]}", username, pwd_hash, "admin", now, 1),
            )
            conn.commit()
            logger.info("[SQLiteBackend] 默认 %s 用户已创建 (bcrypt)", username)

    # ── 用户操作 ──────────────────────────────────────────────────────

    def get_user(self, username: str) -> UserModel | None:
        row = self._get_conn().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: str) -> UserModel | None:
        row = self._get_conn().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def create_user(self, user: UserModel) -> UserModel:
        self._get_conn().execute(
            "INSERT INTO users (id, username, password_hash, role, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.username, user.password_hash, user.role, user.created_at, int(user.is_active)),
        )
        self._get_conn().commit()
        return user

    def update_user(self, user: UserModel) -> UserModel:
        cur = self._get_conn().execute(
            "UPDATE users SET username=?, password_hash=?, role=?, is_active=? WHERE id=?",
            (user.username, user.password_hash, user.role, int(user.is_active), user.id),
        )
        self._get_conn().commit()
        if cur.rowcount == 0:
            raise ValueError(f"用户不存在: {user.id}")
        return user

    def delete_user(self, user_id: str) -> bool:
        cur = self._get_conn().execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._get_conn().commit()
        return cur.rowcount > 0

    def list_users(self, page: int = 1, page_size: int = 20) -> tuple[list[UserModel], int]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
        return [self._row_to_user(r) for r in rows], total

    def user_exists(self, username: str) -> bool:
        row = self._get_conn().execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row is not None

    # ── 审计日志 ──────────────────────────────────────────────────────

    def save_audit_log(self, log: AuditLogModel) -> AuditLogModel:
        self._get_conn().execute(
            "INSERT INTO audit_logs (id, action, username, resource, detail, status, ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (log.id, log.action, log.username, log.resource, log.detail, log.status, log.ip, log.created_at),
        )
        self._get_conn().commit()
        # 保留最近 10000 条
        self._get_conn().execute(
            "DELETE FROM audit_logs WHERE id NOT IN (SELECT id FROM audit_logs ORDER BY created_at DESC LIMIT 10000)"
        )
        self._get_conn().commit()
        return log

    def query_audit_logs(
        self,
        action: str | None = None,
        username: str | None = None,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditLogModel], int]:
        conn = self._get_conn()
        conditions = []
        params = []

        if action:
            conditions.append("action = ?")
            params.append(action)
        if username:
            conditions.append("username = ?")
            params.append(username)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if start_time:
            conditions.append("created_at >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("created_at <= ?")
            params.append(end_time)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        count_sql = f"SELECT COUNT(*) as cnt FROM audit_logs {where}"
        total = conn.execute(count_sql, params).fetchone()["cnt"]

        offset = (page - 1) * page_size
        query_sql = f"SELECT * FROM audit_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        rows = conn.execute(query_sql, params + [page_size, offset]).fetchall()

        logs = [AuditLogModel(**dict(r)) for r in rows]
        return logs, total

    # ── 健康检查 ──────────────────────────────────────────────────────

    def health_check(self) -> dict:
        try:
            row = self._get_conn().execute("SELECT COUNT(*) as cnt FROM users").fetchone()
            return {"backend": "sqlite", "status": "ok", "users_count": dict(row)["cnt"],
                    "db_path": self._db_path}
        except Exception as e:
            return {"backend": "sqlite", "status": "error", "error": str(e)}

    # ── 工具 ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> UserModel:
        d = dict(row)
        d["is_active"] = bool(d.get("is_active", 1))
        return UserModel(**d)
