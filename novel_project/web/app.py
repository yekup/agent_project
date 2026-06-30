"""
FastAPI 主入口（开发模式：无登录）
"""
import os
import sys
from contextlib import asynccontextmanager

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
except ImportError:
    import logging as logger
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── 模板 ────────────────────────────────────────────────────────────
template_dir = os.path.join(BASE_DIR, "web", "templates")
env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)

def render_template(name: str, **kwargs):
    template = env.get_template(name)
    return HTMLResponse(template.render(**kwargs))

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
    app.state.config = load_config()
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
