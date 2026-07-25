"""
数据库抽象接口
============
所有数据库操作通过此接口，与具体后端解耦。

MySQL 迁移时只需写 MySQLBackend 实现此接口，
调用方代码无需改动。
"""
from __future__ import annotations

import abc
from typing import Any

from core.db.models import UserModel, AuditLogModel


class DBBackend(abc.ABC):
    """数据库后端抽象接口"""

    # ── 用户 ─────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_user(self, username: str) -> UserModel | None:
        """按用户名查找用户"""
        ...

    @abc.abstractmethod
    def get_user_by_id(self, user_id: str) -> UserModel | None:
        """按 ID 查找用户"""
        ...

    @abc.abstractmethod
    def create_user(self, user: UserModel) -> UserModel:
        """创建用户"""
        ...

    @abc.abstractmethod
    def update_user(self, user: UserModel) -> UserModel:
        """更新用户（角色/状态等）"""
        ...

    @abc.abstractmethod
    def delete_user(self, user_id: str) -> bool:
        """删除用户"""
        ...

    @abc.abstractmethod
    def list_users(self, page: int = 1, page_size: int = 20) -> tuple[list[UserModel], int]:
        """用户列表，返回 (用户列表, 总数)"""
        ...

    @abc.abstractmethod
    def user_exists(self, username: str) -> bool:
        """检查用户名是否已存在"""
        ...

    # ── 审计日志 ─────────────────────────────────────────────────

    @abc.abstractmethod
    def save_audit_log(self, log: AuditLogModel) -> AuditLogModel:
        """写入审计日志"""
        ...

    @abc.abstractmethod
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
        """查询审计日志，支持按操作/用户/状态/时间筛选"""
        ...

    # ── 健康检查 ─────────────────────────────────────────────────

    @abc.abstractmethod
    def health_check(self) -> dict:
        """后端健康状态"""
        ...
