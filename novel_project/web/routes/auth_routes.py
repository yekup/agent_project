"""
Auth API 路由
=============
提供登录、注册、当前用户信息接口。
用户数据存储在 data/users.json。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from core.security import JWTHandler, ROLE_HIERARCHY

router = APIRouter(tags=["Auth"])

# ── 用户存储 ────────────────────────────────────────────────────────────
USERS_PATH = os.path.join(BASE_DIR, "data", "users.json")

def _load_users() -> dict:
    """加载用户数据"""
    if os.path.exists(USERS_PATH):
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": []}

def _save_users(data: dict):
    """保存用户数据"""
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _hash_password(password: str) -> str:
    """简单密码哈希（生产环境请用 bcrypt）"""
    return hashlib.sha256(password.encode()).hexdigest()

def _ensure_admin_exists():
    """确保默认管理员存在"""
    data = _load_users()
    if not any(u["username"] == "admin" for u in data["users"]):
        data["users"].append({
            "id": "u_admin",
            "username": "admin",
            "password": _hash_password("admin123"),
            "role": "admin",
            "created_at": datetime.now().isoformat(),
        })
        _save_users(data)

_ensure_admin_exists()


# ── 权限映射 ────────────────────────────────────────────────────────────

def get_permissions(role: str) -> dict[str, bool]:
    """
    根据角色返回权限列表。

    前端根据此 dict 的 key 决定 DOM 元素的显隐。
    规则:
        - admin:  全部 true
        - editor: 除管理功能外全部 true
        - viewer: 仅只读功能
        - api:    全部 false（API 用户无页面访问）
    """
    level = ROLE_HIERARCHY.get(role, 0)
    permissions = {
        # 页面
        "page:dashboard": level >= 5,
        "page:graph": level >= 5,
        "page:chat": level >= 5,
        "page:upload": level >= 50,
        "page:admin": level >= 100,
        # 操作
        "action:build": level >= 50,
        "action:delete": level >= 50,
        "action:export": level >= 50,
        "action:admin:users": level >= 100,
        "action:admin:config": level >= 100,
    }
    return permissions


# ── 请求模型 ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


# ── API 路由 ────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(body: LoginRequest):
    """用户登录，返回 JWT token"""
    data = _load_users()
    hashed = _hash_password(body.password)

    user = None
    for u in data["users"]:
        if u["username"] == body.username and u["password"] == hashed:
            user = u
            break

    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    jwt = JWTHandler.get_default()
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
    }
    token = jwt.encode(payload)

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        },
    }


@router.post("/auth/register")
async def register(body: RegisterRequest):
    """用户注册（仅 admin 可用，暂开放）"""
    data = _load_users()

    if any(u["username"] == body.username for u in data["users"]):
        raise HTTPException(status_code=400, detail="用户名已存在")

    if body.role not in ROLE_HIERARCHY:
        raise HTTPException(status_code=400, detail=f"无效角色: {body.role}，可选: {list(ROLE_HIERARCHY.keys())}")

    user_id = f"u_{uuid.uuid4().hex[:8]}"
    new_user = {
        "id": user_id,
        "username": body.username,
        "password": _hash_password(body.password),
        "role": body.role,
        "created_at": datetime.now().isoformat(),
    }
    data["users"].append(new_user)
    _save_users(data)

    return {"id": user_id, "username": body.username, "role": body.role}


@router.get("/auth/me")
async def get_current_user(request: Request):
    """
    获取当前用户信息和权限列表。

    前端据此决定导航栏/按钮的显隐。
    """
    from core.security import JWTHandler
    jwt = JWTHandler.get_default()

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return {
            "user": None,
            "permissions": get_permissions(""),
            "authenticated": False,
        }

    token = auth[7:]
    payload = jwt.decode(token)
    if payload is None:
        return {
            "user": None,
            "permissions": get_permissions(""),
            "authenticated": False,
        }

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
    from core.security import require_role, JWTHandler
    # 权限校验
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未认证")
    payload = JWTHandler.get_default().decode(auth[7:])
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看用户列表")

    data = _load_users()
    safe_users = [
        {"id": u["id"], "username": u["username"], "role": u["role"], "created_at": u.get("created_at", "")}
        for u in data["users"]
    ]
    return {"users": safe_users}
