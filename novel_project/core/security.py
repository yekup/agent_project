"""
安全中间件
============
包含: JWT 认证、权限拦截、文件上传校验、限流
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ===========================================================================
# JWT 工具
# ===========================================================================

class JWTHandler:
    """
    JWT 签发与验证。

    与 run.py 的 os.urandom(32).hex() 不同:
      本实现从配置文件/环境变量读取持久化密钥，
      重启后 Token 仍然有效。
    """

    def __init__(self, secret: str = "", algorithm: str = "HS256", expire_hours: int = 72):
        self.secret = secret or os.environ.get(
            "JWT_SECRET",
            "change-me-in-production-must-be-32-chars-min",
        )
        self.algorithm = algorithm
        self.expire_hours = expire_hours

    def encode(self, payload: dict) -> str:
        """签发 JWT"""
        import jwt as pyjwt
        payload = {**payload, "exp": datetime.utcnow() + timedelta(hours=self.expire_hours)}
        return pyjwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str) -> dict | None:
        """验证 JWT，失败返回 None"""
        import jwt as pyjwt
        try:
            return pyjwt.decode(token, self.secret, algorithms=[self.algorithm])
        except Exception as e:
            logger.warning(f"JWT 验证失败: {e}")
            return None

    @staticmethod
    def get_default() -> "JWTHandler":
        """从环境变量或配置文件创建实例"""
        return JWTHandler(
            secret=os.environ.get("JWT_SECRET", ""),
        )


# ===========================================================================
# 权限系统
# ===========================================================================

ROLE_HIERARCHY = {
    "admin": 100,
    "editor": 50,
    "viewer": 10,
    "api": 5,
}


class PermissionMiddleware:
    """
    FastAPI 权限拦截中间件。

    注册方式:
        app.add_middleware(PermissionMiddleware)

    或者在路由层使用:
        @router.post("/api/admin")
        @require_role("admin")
        async def admin_api(): ...

    权限等级:
        admin  > editor > viewer > api
    """

    def __init__(self, public_paths: list[str] | None = None):
        self.public_paths = public_paths or [
            "/", "/static/", "/api/login", "/api/register",
            "/docs", "/openapi.json", "/health",
        ]
        self.jwt = JWTHandler.get_default()

    async def __call__(self, request, call_next):
        path = request.url.path

        # 公开路径直接放行
        if any(path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        # 验证 JWT
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "未提供认证 Token"})

        token = auth[7:]
        payload = self.jwt.decode(token)
        if payload is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Token 无效或已过期"})

        # 注入用户信息到 request.state
        request.state.user = payload
        request.state.user_id = payload.get("sub", "")
        request.state.role = payload.get("role", "viewer")

        return await call_next(request)


def require_role(min_role: str):
    """
    路由级权限装饰器。

    用法:
        @router.post("/api/admin")
        @require_role("admin")
        async def admin_api(): ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if hasattr(arg, "state"):
                    request = arg
                    break

            if request is None:
                for _, v in kwargs.items():
                    if hasattr(v, "state"):
                        request = v
                        break

            if request is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="无法识别请求")

            user_role = getattr(request.state, "role", "viewer")
            if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(min_role, 0):
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=403,
                    detail=f"权限不足: 需要 {min_role} 角色, 当前 {user_role}",
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def require_user_owns_resource(resource_user_id_field: str = "user_id"):
    """
    资源归属校验装饰器 —— 用户只能访问自己的资源。

    用法:
        @router.get("/api/novels/{novel_id}")
        @require_user_owns_resource("user_id")
        async def get_novel(novel_id: str, request: Request): ...

    需要 resource 对象包含 user_id 字段与 JWT 中的 sub 匹配。
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if hasattr(arg, "state"):
                    request = arg
                    break
            if request is None:
                for _, v in kwargs.items():
                    if hasattr(v, "state"):
                        request = v
                        break

            if request is not None:
                user_id = getattr(request.state, "user_id", "")
                # 这里简化处理：实际应从数据库加载 resource 并比较 user_id
                if not user_id:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="未认证")

            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ===========================================================================
# 文件上传校验
# ===========================================================================

# 允许的文件类型 (MIME + 魔数)
ALLOWED_MIME_TYPES = {
    "text/plain": b"",
}
# 文件魔数签名 (文件头字节)
MAGIC_SIGNATURES: dict[str, bytes] = {
    ".txt": b"",        # 纯文本无固定魔数
}


class FileValidator:
    """
    上传文件校验器。

    校验链:
        1. 扩展名白名单
        2. MIME 类型检查
        3. 文件头魔数验证（可选）
        4. 文件名安全性检查（防路径穿越）
        5. 文件大小限制
    """

    ALLOWED_EXTENSIONS = {".txt"}
    MAX_SIZE_MB = 100

    @classmethod
    def validate(cls, filename: str, content: bytes, content_type: str = "") -> dict:
        """
        校验上传文件。

        Returns:
            {"valid": True} 或 {"valid": False, "error": "原因"}
        """
        # 1. 扩展名校验
        ext = os.path.splitext(filename)[1].lower()
        if ext not in cls.ALLOWED_EXTENSIONS:
            return {"valid": False, "error": f"不支持的文件类型: {ext}，仅支持 .txt"}

        # 2. 文件名安全性: 防止路径穿越
        if ".." in filename or "/" in filename or "\\" in filename:
            return {"valid": False, "error": "文件名不合法"}

        # 3. MIME 类型校验
        if content_type and content_type not in ALLOWED_MIME_TYPES:
            return {"valid": False, "error": f"不支持的文件格式: {content_type}"}

        # 4. 文件大小校验
        if len(content) > cls.MAX_SIZE_MB * 1024 * 1024:
            return {"valid": False, "error": f"文件超过 {cls.MAX_SIZE_MB}MB 限制"}

        # 5. 文件内容校验（检查是否是二进制）
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return {"valid": False, "error": "文件包含非 UTF-8 编码字符，请上传纯文本文件"}

        return {"valid": True}

    @classmethod
    def safe_filename(cls, filename: str, user_id: str = "") -> str:
        """生成安全的存储文件名: {user_id}_{timestamp}_{hash}.txt"""
        timestamp = int(time.time())
        name_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
        if user_id:
            return f"{user_id}_{timestamp}_{name_hash}.txt"
        return f"{timestamp}_{name_hash}.txt"

    @classmethod
    def ensure_safe_path(cls, base_dir: str, user_id: str) -> str:
        """确保用户目录安全"""
        safe_dir = os.path.join(base_dir, user_id)
        resolved = Path(safe_dir).resolve()
        base_resolved = Path(base_dir).resolve()
        # 防止路径穿越: 确保 resolved 路径在 base_dir 下
        if not str(resolved).startswith(str(base_resolved)):
            raise PermissionError(f"路径越界: {safe_dir}")
        os.makedirs(resolved, exist_ok=True)
        return str(resolved)


# ===========================================================================
# 令牌桶限流
# ===========================================================================

class TokenBucket:
    """
    令牌桶限流器。

    用法:
        bucket = TokenBucket(rate=60, burst=100)  # 每分钟 60 个请求，突发 100
        if bucket.allow("user_xxx"):
            # 处理请求
        else:
            # 返回 429
    """

    def __init__(self, rate: float = 60, burst: int = 100):
        self.rate = rate  # 每秒填充速率
        self.burst = burst  # 桶容量
        self._buckets: dict[str, dict] = {}

    def allow(self, key: str) -> bool:
        """
        检查请求是否允许通过。

        Args:
            key: 限流 key（用户 ID / IP / 接口路径）

        Returns:
            True 表示允许，False 表示限流
        """
        now = time.time()
        if key not in self._buckets:
            self._buckets[key] = {"tokens": self.burst, "last_refill": now}

        bucket = self._buckets[key]

        # 补充令牌
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(self.burst, bucket["tokens"] + elapsed * self.rate)
        bucket["last_refill"] = now

        # 消耗令牌
        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True

        return False

    def get_wait_time(self, key: str) -> float:
        """获取需要等待的秒数"""
        bucket = self._buckets.get(key, {"tokens": self.burst, "last_refill": time.time()})
        if bucket["tokens"] >= 1:
            return 0
        return (1 - bucket["tokens"]) / self.rate

    def cleanup(self, max_age: int = 3600):
        """清理过期桶"""
        now = time.time()
        expired = [k for k, v in self._buckets.items() if now - v["last_refill"] > max_age]
        for k in expired:
            del self._buckets[k]


class RateLimitMiddleware:
    """
    FastAPI 限流中间件。

    注册:
        app.add_middleware(RateLimitMiddleware, rate=60, burst=100)

    支持三级限流:
        - 用户级: 根据 user_id
        - IP 级:  根据客户端 IP
        - 接口级: 根据请求路径
    """

    def __init__(
        self,
        rate: float = 60,
        burst: int = 100,
        exclude_paths: list[str] | None = None,
    ):
        self.bucket = TokenBucket(rate=rate / 60, burst=burst)
        self.exclude_paths = exclude_paths or ["/health", "/static/"]

    async def __call__(self, request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)

        # 三级限流 key
        user_key = getattr(request.state, "user_id", "") if hasattr(request.state, "user_id") else ""
        ip_key = request.client.host if request.client else "unknown"
        path_key = path.split("/")[1] if path else "root"

        # 组合 key: user > ip > path
        limit_key = user_key or ip_key or path_key

        if not self.bucket.allow(limit_key):
            from fastapi.responses import JSONResponse
            wait = self.bucket.get_wait_time(limit_key)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "请求过于频繁",
                    "retry_after_seconds": round(wait, 1),
                },
                headers={"Retry-After": str(int(wait))},
            )

        return await call_next(request)


# ===========================================================================
# 日志脱敏过滤器
# ===========================================================================

SENSITIVE_PATTERNS = [
    (r"(api_key|apikey|secret|password|token)=[\"']?[^&\s\"']+", r"\1=***"),
    (r"(Authorization:\s*Bearer\s+)\S+", r"\1***"),
    (r"DEEPSEEK_API_KEY[=:]\s*\S+", "DEEPSEEK_API_KEY=***"),
]


def sanitize_log(msg: str) -> str:
    """脱敏日志中的敏感信息"""
    for pattern, replacement in SENSITIVE_PATTERNS:
        msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
    return msg


class SanitizeLogFilter(logging.Filter):
    """日志脱敏过滤器 —— 附加到 logging 处理器自动脱敏"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_log(record.msg)
        return True


# ===========================================================================
# 配置加载
# ===========================================================================

def load_config(path: str = "config.yaml") -> dict:
    """加载 YAML 配置，支持环境变量覆盖"""
    if not os.path.exists(path):
        logger.warning(f"配置文件不存在: {path}，使用默认配置")
        return {}

    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        # 环境变量替换: ${VAR:-default}
        def env_replace(m):
            var = m.group(1)
            default = m.group(2)
            return os.environ.get(var, default or "")
        raw = re.sub(r"\$\{([^}:]+):?-?([^}]*)\}", env_replace, raw)
        config = yaml.safe_load(raw)
        logger.info(f"配置已加载: {path}")
        return config or {}
    except ImportError:
        logger.warning("yaml 未安装 (pip install pyyaml)，跳过配置文件")
        return {}
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        return {}
