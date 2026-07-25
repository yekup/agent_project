"""
数据模型
=======
所有 DB 操作使用这些模型，后端无关。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.security import ROLE_HIERARCHY


@dataclass
class UserModel:
    """用户模型"""
    id: str
    username: str
    password_hash: str = ""
    role: str = "viewer"
    created_at: str = ""
    is_active: bool = True

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def role_level(self) -> int:
        return ROLE_HIERARCHY.get(self.role, 0)

    def can(self, min_role: str) -> bool:
        return self.role_level >= ROLE_HIERARCHY.get(min_role, 0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at,
            "is_active": self.is_active,
        }


@dataclass
class AuditLogModel:
    """审计日志模型"""
    id: str = ""
    action: str = ""          # login / upload / build / delete / edit
    username: str = ""
    resource: str = ""        # 操作对象
    detail: str = ""
    status: str = "success"   # success / failure / denied
    ip: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = f"log_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "username": self.username,
            "resource": self.resource,
            "detail": self.detail,
            "status": self.status,
            "ip": self.ip,
            "created_at": self.created_at,
        }
