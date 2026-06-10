"""
异步编译管道
管理编译进度，支持前端轮询
"""
import json
import os
from datetime import datetime

PIPELINE_DIR = "data/pipeline"


def init_pipeline(novel_name):
    """初始化编译任务"""
    os.makedirs(PIPELINE_DIR, exist_ok=True)
    status = {
        "novel_name": novel_name,
        "status": "pending",
        "total_chapters": 0,
        "completed_chapters": 0,
        "current_chapter": "",
        "message": "",
        "phase": "waiting",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_status(novel_name, status)
    return status


def update_status(novel_name, **kwargs):
    """更新编译状态"""
    status = _load_status(novel_name)
    if not status:
        return
    status.update(kwargs)
    status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_status(novel_name, status)


def get_status(novel_name):
    """获取编译状态"""
    return _load_status(novel_name)


def _save_status(novel_name, data):
    path = os.path.join(PIPELINE_DIR, f"{novel_name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_status(novel_name):
    path = os.path.join(PIPELINE_DIR, f"{novel_name}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
