"""
FastAPI 主入口（开发模式：无登录）
"""
import os
import sys
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

template_dir = os.path.join(BASE_DIR, "web", "templates")
env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)

def render_template(name: str, **kwargs):
    template = env.get_template(name)
    return HTMLResponse(template.render(**kwargs))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("项目三启动完成（开发模式）")
    yield
    logger.info("项目三关闭")


app = FastAPI(title="网文 GraphRAG 分析系统", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "web", "static")), name="static")

from web.routes import agent_routes
app.include_router(agent_routes.router, prefix="/api", tags=["API"])


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
