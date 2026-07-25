"""
JSON 文件后端
============
当前默认实现，所有数据存储在 JSON 文件中。

文件结构:
    data/
    ├── users.json       # 用户表
    └── logs/
        ├── audit.json   # 审计日志（分页存储）
        └── audit_*.log  # 按日滚动
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from core.db.interface import DBBackend
from core.db.models import UserModel, AuditLogModel

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"


class JsonBackend(DBBackend):
    """JSON 文件后端（threading.Lock 跨线程 + FileLock 跨进程）"""

    def __init__(self, data_dir: str | Path | None = None):
        self._dir = Path(data_dir) if data_dir else DATA_DIR
        self._users_path = self._dir / "users.json"
        self._logs_dir = self._dir / "logs"
        self._logs_path = self._logs_dir / "audit.json"
        self._lock = threading.Lock()
        self._users_flock = FileLock(str(self._users_path) + ".lock")
        self._logs_flock = FileLock(str(self._logs_path) + ".lock")

        self._ensure_dirs()
        self._migrate_old_users()

    # ── 初始化 ─────────────────────────────────────────────────

    def _ensure_dirs(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_old_users(self):
        """迁移旧版 users.json 格式到新版模型"""
        if not self._users_path.exists():
            self._init_default_users()
            return
        try:
            with open(self._users_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 旧格式：{"users": [...]} → 新格式：直接列表
            if isinstance(data, dict) and "users" in data:
                users = data["users"]
            elif isinstance(data, list):
                users = data
            else:
                return

            # 重命名字段: password → password_hash
            changed = False
            for u in users:
                if "password" in u and "password_hash" not in u:
                    u["password_hash"] = u.pop("password")
                    changed = True

            if isinstance(data, dict):
                self._write_json(self._users_path, users)
            elif changed:
                self._write_json(self._users_path, users)
        except Exception:
            pass

    def _init_default_users(self):
        """
        创建默认 admin 用户。

        初始密码优先级: 环境变量 ADMIN_PASSWORD → 随机生成（打印到控制台）。
        不再使用源码内硬编码的弱口令。
        """
        import logging
        import secrets
        import uuid

        from core.security import hash_password

        password = os.environ.get("ADMIN_PASSWORD", "").strip()
        if password:
            logging.getLogger(__name__).info("默认 admin 密码来自环境变量 ADMIN_PASSWORD")
        else:
            password = secrets.token_urlsafe(12)
            # 首次初始化的唯一凭据获取渠道，必须让人看到
            print(f"[初始化] 已创建默认 admin 账号，随机密码: {password} （请立即登录并修改）")
            logging.getLogger(__name__).warning("默认 admin 随机密码: %s", password)

        admin = UserModel(
            id=f"u_{uuid.uuid4().hex[:8]}",
            username=os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin",
            password_hash=hash_password(password),
            role="admin",
            created_at=datetime.now().isoformat(),
        )
        data = admin.to_dict()
        data["password_hash"] = admin.password_hash
        self._write_json(self._users_path, [data])

    # ── 读写工具 ───────────────────────────────────────────────

    def _read_json(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "users" in data:
                return data["users"]
            return []
        except Exception:
            return []

    def _write_json(self, path: Path, data: list[dict]):
        """原子写入：临时文件 + os.replace，避免崩溃留下截断的空文件"""
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    # ── 用户操作 ───────────────────────────────────────────────

    def get_user(self, username: str) -> UserModel | None:
        with self._lock:
            users = self._read_json(self._users_path)
        for u in users:
            if u.get("username") == username:
                return self._dict_to_user(u)
        return None

    def get_user_by_id(self, user_id: str) -> UserModel | None:
        with self._lock:
            users = self._read_json(self._users_path)
        for u in users:
            if u.get("id") == user_id:
                return self._dict_to_user(u)
        return None

    def create_user(self, user: UserModel) -> UserModel:
        with self._lock, self._users_flock:
            users = self._read_json(self._users_path)
            users.append(user.to_dict() | {"password_hash": user.password_hash})
            self._write_json(self._users_path, users)
        return user

    def update_user(self, user: UserModel) -> UserModel:
        with self._lock, self._users_flock:
            users = self._read_json(self._users_path)
            for i, u in enumerate(users):
                if u.get("id") == user.id:
                    users[i] = user.to_dict() | {"password_hash": user.password_hash}
                    self._write_json(self._users_path, users)
                    return user
        raise ValueError(f"用户不存在: {user.id}")

    def delete_user(self, user_id: str) -> bool:
        with self._lock, self._users_flock:
            users = self._read_json(self._users_path)
            new_users = [u for u in users if u.get("id") != user_id]
            if len(new_users) == len(users):
                return False
            self._write_json(self._users_path, new_users)
        return True

    def list_users(self, page: int = 1, page_size: int = 20) -> tuple[list[UserModel], int]:
        with self._lock:
            users = self._read_json(self._users_path)
        total = len(users)
        start = (page - 1) * page_size
        end = start + page_size
        items = [self._dict_to_user(u) for u in users[start:end]]
        return items, total

    def user_exists(self, username: str) -> bool:
        return self.get_user(username) is not None

    # ── 审计日志 ───────────────────────────────────────────────

    def save_audit_log(self, log: AuditLogModel) -> AuditLogModel:
        with self._lock, self._logs_flock:
            logs = self._read_json(self._logs_path)
            logs.append(log.to_dict())
            # 保留最近 10000 条
            if len(logs) > 10000:
                logs = logs[-10000:]
            self._write_json(self._logs_path, logs)
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
        with self._lock:
            all_logs = self._read_json(self._logs_path)

        # 筛选
        filtered = []
        for l in all_logs:
            if action and l.get("action") != action:
                continue
            if username and l.get("username") != username:
                continue
            if status and l.get("status") != status:
                continue
            if start_time and l.get("created_at", "") < start_time:
                continue
            if end_time and l.get("created_at", "") > end_time:
                continue
            filtered.append(l)

        # 按时间倒序
        filtered.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        items = [AuditLogModel(**l) for l in filtered[start:end]]
        return items, total

    # ── 健康检查 ───────────────────────────────────────────────

    def health_check(self) -> dict:
        try:
            users, _ = self.list_users(page=1, page_size=1)
            return {
                "backend": "json",
                "status": "ok",
                "users_count": 0,
                "users_path": str(self._users_path),
                "logs_path": str(self._logs_path),
            }
        except Exception as e:
            return {"backend": "json", "status": "error", "error": str(e)}

    # ── 工具 ───────────────────────────────────────────────────

    @staticmethod
    def _dict_to_user(d: dict) -> UserModel:
        return UserModel(
            id=d.get("id", ""),
            username=d.get("username", ""),
            password_hash=d.get("password_hash") or d.get("password", ""),
            role=d.get("role", "viewer"),
            created_at=d.get("created_at", ""),
            is_active=d.get("is_active", True),
        )
