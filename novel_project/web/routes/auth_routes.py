"""
Auth API 路由
=============
提供登录、注册、当前用户信息接口。
数据持久化通过 DBBackend 抽象层，当前使用 JSON 文件，未来可切 MySQL。
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.db import get_db
from core.db.models import UserModel, AuditLogModel
from core.security import (
    JWTHandler, ROLE_HIERARCHY,
    hash_password, verify_password, is_legacy_hash,
)

router = APIRouter(tags=["Auth"])


# ── 权限映射 ────────────────────────────────────────────────────────────

def get_permissions(role: str) -> dict[str, bool]:
    """根据角色返回权限列表"""
    level = ROLE_HIERARCHY.get(role, 0)
    return {
        "page:dashboard": level >= 5,
        "page:graph": level >= 5,
        "page:chat": level >= 5,
        "page:upload": level >= 50,
        "page:admin": level >= 100,
        "action:build": level >= 50,
        "action:delete": level >= 50,
        "action:export": level >= 50,
        "action:edit": level >= 50,
        "action:admin:users": level >= 100,
        "action:admin:config": level >= 100,
    }


# ── 请求模型 ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    # 兼容旧前端字段，服务器端强制忽略 —— 自助注册一律为 viewer，
    # 角色变更只能由 admin 操作（防止注册即提权）。
    role: str = "viewer"


# ── API 路由 ────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(body: LoginRequest):
    """用户登录，返回 JWT token"""
    db = get_db()
    user = db.get_user(body.username)

    if not user or not verify_password(body.password, user.password_hash):
        db.save_audit_log(AuditLogModel(
            action="login", username=body.username, status="failure",
            detail="用户名或密码错误",
        ))
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        db.save_audit_log(AuditLogModel(
            action="login", username=body.username, status="failure",
            detail="账号已被禁用",
        ))
        raise HTTPException(status_code=403, detail="账号已被禁用")

    # 旧版 sha256 哈希 → 登录成功后透明迁移为 bcrypt
    if is_legacy_hash(user.password_hash):
        try:
            user.password_hash = hash_password(body.password)
            db.update_user(user)
        except Exception:
            pass  # 迁移失败不影响本次登录

    jwt = JWTHandler.get_default()
    token = jwt.encode({"sub": user.id, "username": user.username, "role": user.role})

    db.save_audit_log(AuditLogModel(
        action="login", username=user.username, status="success",
    ))

    return {
        "token": token,
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


@router.post("/auth/register")
async def register(body: RegisterRequest):
    """用户注册（自助注册固定为 viewer 角色）"""
    db = get_db()

    if db.user_exists(body.username):
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = UserModel(
        id=f"u_{uuid.uuid4().hex[:8]}",
        username=body.username,
        password_hash=hash_password(body.password),
        role="viewer",
    )
    db.create_user(user)

    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/auth/me")
async def get_current_user(request: Request):
    """获取当前用户信息和权限列表"""
    jwt = JWTHandler.get_default()

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return {"user": None, "permissions": get_permissions(""), "authenticated": False}

    payload = jwt.decode(auth[7:])
    if payload is None:
        return {"user": None, "permissions": get_permissions(""), "authenticated": False}

    return {
        "user": {
            "id": payload.get("sub", ""),
            "username": payload.get("username", ""),
            "role": payload.get("role", ""),
        },
        "permissions": get_permissions(payload.get("role", "")),
        "authenticated": True,
    }


@router.get("/auth/users")
async def list_users(request: Request):
    """列出所有用户（仅 admin）"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未认证")

    payload = JWTHandler.get_default().decode(auth[7:])
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看用户列表")

    db = get_db()
    users, total = db.list_users(page=1, page_size=200)

    return {
        "users": [u.to_dict() for u in users],
        "total": total,
    }
