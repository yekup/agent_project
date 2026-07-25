"""
MySQL 后端
==========
基于 PyMySQL 的 DBBackend 实现，语义与 JsonBackend 对齐。

表结构以 SQL/schema.sql 为准（users / audit_logs）。

连接管理:
    - 惰性连接：首次执行 SQL 时才建立连接
    - 自动重连：每次取连接时 ping(reconnect=True)
    - DictCursor：查询结果以 dict 行返回

配置来源（优先级）:
    1. 构造参数 config dict（host/port/user/password/database）
    2. config.yaml 的 db.mysql 节（支持 ${ENV_VAR:-default} 覆盖）

切换方式:
    config.yaml 中设置 db.backend: mysql，get_db() 会自动装配本后端；
    或手动 set_db(MySQLBackend(mysql_config))。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from core.db.interface import DBBackend
from core.db.models import UserModel, AuditLogModel

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]

# 审计日志保留条数上限，与 JsonBackend 一致
MAX_AUDIT_LOGS = 10000

_USER_COLUMNS = "id, username, password_hash, role, created_at, is_active"
_LOG_COLUMNS = "id, action, username, resource, detail, status, ip, created_at"


class MySQLBackend(DBBackend):
    """MySQL 后端（PyMySQL，全部参数化查询）"""

    def __init__(self, config: dict | None = None):
        if config is None:
            config = self._load_config_from_yaml()
        self._config = config
        self._conn = None
        self._lock = threading.Lock()

    @staticmethod
    def _load_config_from_yaml() -> dict:
        """从 config.yaml 的 db.mysql 节读取连接配置"""
        try:
            from core.security import load_config
            cfg = load_config(str(REPO_ROOT / "config.yaml"))
            return (cfg.get("db") or {}).get("mysql") or {}
        except Exception as e:
            logger.warning("读取 config.yaml db.mysql 配置失败: %s", e)
            return {}

    # ── 连接管理 ───────────────────────────────────────────────

    def _get_conn(self):
        """惰性建连 + 自动重连（线程安全）"""
        with self._lock:
            if self._conn is None:
                self._conn = pymysql.connect(
                    host=self._config.get("host", "127.0.0.1"),
                    port=int(self._config.get("port", 3306)),
                    user=self._config.get("user", "root"),
                    password=self._config.get("password", ""),
                    database=self._config.get("database", "novel_graphrag"),
                    charset="utf8mb4",
                    cursorclass=DictCursor,
                    autocommit=True,
                )
            else:
                self._conn.ping(reconnect=True)
            return self._conn

    def close(self):
        """显式关闭连接（下次使用时自动重连）"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    # ── SQL 执行工具 ───────────────────────────────────────────

    def _query_one(self, sql: str, params: tuple = ()) -> dict | None:
        cur = self._get_conn().cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchone()
        finally:
            cur.close()

    def _query_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._get_conn().cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            cur.close()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回受影响行数"""
        cur = self._get_conn().cursor()
        try:
            cur.execute(sql, params)
            return cur.rowcount
        finally:
            cur.close()

    # ── 用户 ───────────────────────────────────────────────────

    def get_user(self, username: str) -> UserModel | None:
        row = self._query_one(
            f"SELECT {_USER_COLUMNS} FROM users WHERE username = %s",
            (username,),
        )
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: str) -> UserModel | None:
        row = self._query_one(
            f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",
            (user_id,),
        )
        return self._row_to_user(row) if row else None

    def create_user(self, user: UserModel) -> UserModel:
        self._execute(
            "INSERT INTO users (id, username, password_hash, role, created_at, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user.id, user.username, user.password_hash,
             user.role, user.created_at, user.is_active),
        )
        return user

    def update_user(self, user: UserModel) -> UserModel:
        rowcount = self._execute(
            "UPDATE users SET username = %s, password_hash = %s, role = %s, "
            "created_at = %s, is_active = %s WHERE id = %s",
            (user.username, user.password_hash, user.role,
             user.created_at, user.is_active, user.id),
        )
        if rowcount == 0 and self.get_user_by_id(user.id) is None:
            raise ValueError(f"用户不存在: {user.id}")
        return user

    def delete_user(self, user_id: str) -> bool:
        rowcount = self._execute(
            "DELETE FROM users WHERE id = %s",
            (user_id,),
        )
        return rowcount > 0

    def list_users(self, page: int = 1, page_size: int = 20) -> tuple[list[UserModel], int]:
        row = self._query_one("SELECT COUNT(*) AS cnt FROM users")
        total = int(row["cnt"]) if row else 0
        rows = self._query_all(
            f"SELECT {_USER_COLUMNS} FROM users "
            "ORDER BY created_at ASC, id ASC LIMIT %s OFFSET %s",
            (page_size, (page - 1) * page_size),
        )
        return [self._row_to_user(r) for r in rows], total

    def user_exists(self, username: str) -> bool:
        row = self._query_one(
            "SELECT 1 AS one FROM users WHERE username = %s LIMIT 1",
            (username,),
        )
        return row is not None

    # ── 审计日志 ───────────────────────────────────────────────

    def save_audit_log(self, log: AuditLogModel) -> AuditLogModel:
        self._execute(
            "INSERT INTO audit_logs (id, action, username, resource, detail, status, ip, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (log.id, log.action, log.username, log.resource,
             log.detail, log.status, log.ip, log.created_at),
        )
        # 裁剪：只保留最近 MAX_AUDIT_LOGS 条（按 created_at 倒序，取交集外的删除）
        self._execute(
            "DELETE FROM audit_logs WHERE id NOT IN ("
            "  SELECT id FROM ("
            "    SELECT id FROM audit_logs "
            "    ORDER BY created_at DESC, id DESC LIMIT %s"
            "  ) AS recent"
            ")",
            (MAX_AUDIT_LOGS,),
        )
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
        where, params = [], []
        if action:
            where.append("action = %s")
            params.append(action)
        if username:
            where.append("username = %s")
            params.append(username)
        if status:
            where.append("status = %s")
            params.append(status)
        if start_time:
            # DATETIME 列与 ISO 字符串比较前去掉 "T"
            where.append("created_at >= %s")
            params.append(start_time.replace("T", " "))
        if end_time:
            where.append("created_at <= %s")
            params.append(end_time.replace("T", " "))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        row = self._query_one(
            f"SELECT COUNT(*) AS cnt FROM audit_logs{where_sql}",
            tuple(params),
        )
        total = int(row["cnt"]) if row else 0

        rows = self._query_all(
            f"SELECT {_LOG_COLUMNS} FROM audit_logs{where_sql} "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            tuple(params) + (page_size, (page - 1) * page_size),
        )
        return [self._row_to_log(r) for r in rows], total

    # ── 健康检查 ───────────────────────────────────────────────

    def health_check(self) -> dict:
        try:
            row = self._query_one("SELECT COUNT(*) AS cnt FROM users")
            return {
                "backend": "mysql",
                "status": "ok",
                "users_count": int(row["cnt"]) if row else 0,
                "host": self._config.get("host"),
                "database": self._config.get("database"),
            }
        except Exception as e:
            return {"backend": "mysql", "status": "error", "error": str(e)}

    # ── 行转换 ─────────────────────────────────────────────────

    @staticmethod
    def _fmt_time(v) -> str:
        """DATETIME 列 → ISO 字符串，与 JsonBackend 的字符串语义对齐"""
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    @classmethod
    def _row_to_user(cls, row: dict) -> UserModel:
        return UserModel(
            id=row.get("id", ""),
            username=row.get("username", ""),
            password_hash=row.get("password_hash", ""),
            role=row.get("role", "viewer"),
            created_at=cls._fmt_time(row.get("created_at")),
            is_active=bool(row.get("is_active", True)),
        )

    @classmethod
    def _row_to_log(cls, row: dict) -> AuditLogModel:
        return AuditLogModel(
            id=row.get("id", ""),
            action=row.get("action", ""),
            username=row.get("username", ""),
            resource=row.get("resource", ""),
            detail=row.get("detail") or "",
            status=row.get("status", "success"),
            ip=row.get("ip", ""),
            created_at=cls._fmt_time(row.get("created_at")),
        )
