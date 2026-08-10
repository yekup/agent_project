"""
FastAPI 主入口（开发模式：无登录）
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 尽早加载项目根目录 .env（JWT_SECRET、ADMIN_PASSWORD、API key 等），
# 不覆盖真实环境变量（docker/CI 注入优先）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

# ── 日志 ────────────────────────────────────────────────────────────
try:
    from loguru import logger
    logger.add(
        "data/logs/app_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="INFO",
        format="{time} {level} {message}",
    )

    # 标准库 logging → loguru 桥接：core/* 模块的 logger 输出统一进 loguru
    import logging

    class _InterceptHandler(logging.Handler):
        def emit(self, record):
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            frame, depth = logging.currentframe(), 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1
            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    _intercept = _InterceptHandler()
    try:
        from core.security import SanitizeLogFilter
        _intercept.addFilter(SanitizeLogFilter())
    except Exception:
        pass
    logging.basicConfig(handlers=[_intercept], level=logging.INFO, force=True)
except ImportError:
    import logging as logger
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── 模板 ────────────────────────────────────────────────────────────
template_dir = os.path.join(BASE_DIR, "web", "templates")
env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)

def render_template(name: str, **kwargs):
    template = env.get_template(name)
    # no-cache：页面 HTML 每次重新请求，避免浏览器长期缓存旧页面
    # （旧页面内联脚本不带 token 调 /api，会被权限中间件 401 踢回登录页）
    return HTMLResponse(template.render(**kwargs), headers={"Cache-Control": "no-cache"})

# ── 配置加载 ─────────────────────────────────────────────────────────
def load_config():
    """加载 config.yaml，返回配置 dict"""
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(BASE_DIR), "config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                raw = f.read()
            import re
            def env_replace(m):
                var = m.group(1)
                default = m.group(2)
                return os.environ.get(var, default or "")
            raw = re.sub(r"\$\{([^}:]+):?-?([^}]*)\}", env_replace, raw)
            config = yaml.safe_load(raw)
            logger.info(f"配置已加载: {config_path}")
            return config or {}
        else:
            logger.warning(f"配置文件不存在: {config_path}")
            return {}
    except ImportError:
        logger.warning("pyyaml 未安装，跳过配置加载")
        return {}
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        return {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    app.state.config = _config
    try:
        from core.db import get_db
        get_db()  # 初始化 DB 后端
    except Exception as e:
        logger.warning(f"DB 初始化失败: {e}")
    logger.info("网文 GraphRAG 分析系统启动完成")
    yield
    # 关闭时
    logger.info("网文 GraphRAG 分析系统关闭")

app = FastAPI(title="网文 GraphRAG 分析系统", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "web", "static")), name="static")

from web.routes import agent_routes
from web.routes import auth_routes
app.include_router(agent_routes.router, prefix="/api", tags=["API"])
app.include_router(auth_routes.router, prefix="/api", tags=["Auth"])

# 模块级加载一次配置，供中间件注册与 lifespan 共用
_config = load_config()

# ── 限流中间件（内层：权限之后执行，可按 user_id 限流）──────────────
_rl_cfg = (_config.get("security") or {}).get("rate_limit") or {}
if _rl_cfg.get("enabled", True):
    try:
        from core.security import RateLimitMiddleware
        app.add_middleware(
            RateLimitMiddleware,
            rate=float(_rl_cfg.get("requests_per_minute", 60)),
            burst=int(_rl_cfg.get("burst", 100)),
        )
        logger.info("限流中间件已注册")
    except Exception as e:
        logger.warning(f"限流中间件注册失败: {e}")

# ── 权限中间件（外层：先跑，注入 user_id/role）─────────────────────
try:
    from core.security import PermissionMiddleware
    app.add_middleware(PermissionMiddleware)
    logger.info("权限中间件已注册")
except Exception as e:
    logger.warning(f"权限中间件注册失败: {e}")

# ── CORS（最外层：先处理预检请求；"*" 不与 credentials 同用）───────
_cors_origins = (_config.get("security") or {}).get("cors_origins") or []
if _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info(f"CORS 已启用: {_cors_origins}")


# ── 健康检查 ────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": "novel-graphrag",
        "version": "0.2.0",
    }


# ── 页面路由 ────────────────────────────────────────────────────────
@app.get("/")
async def index(request: Request):
    return render_template("dashboard.html", request=request)

@app.get("/graph")
async def graph_page(request: Request):
    return render_template("graph.html", request=request)

@app.get("/chat")
async def chat_page(request: Request):
    return render_template("chat.html", request=request)

@app.get("/upload")
async def upload_page(request: Request):
    return render_template("upload.html", request=request)

@app.get("/login")
async def login_page(request: Request):
    return render_template("login.html", request=request)
