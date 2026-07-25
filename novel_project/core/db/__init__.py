"""
数据库抽象层
==========
支持 JSON 文件、SQLite、MySQL 三种后端。

config.yaml 设置 db.backend 选择:
  - sqlite (默认): SQLite + WAL 模式，多进程安全
  - json: JSON 文件，单机简单场景
  - mysql: MySQL，生产多实例

用法:
    from core.db import get_db
    db = get_db()
    user = db.get_user("admin")
"""
from pathlib import Path

from core.db.interface import DBBackend, UserModel, AuditLogModel
from core.db.json_backend import JsonBackend
from core.db.sqlite_backend import SQLiteBackend

_backend: DBBackend | None = None

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _create_backend() -> DBBackend:
    """按 config.yaml 的 db.backend 选择后端，默认 sqlite。"""
    try:
        from core.security import load_config
        cfg = load_config(str(_REPO_ROOT / "config.yaml"))
        db_cfg = cfg.get("db") or {}
        backend_name = str(db_cfg.get("backend", "sqlite")).strip().lower()

        if backend_name == "mysql":
            from core.db.mysql_backend import MySQLBackend
            return MySQLBackend(db_cfg.get("mysql"))
        elif backend_name == "json":
            return JsonBackend()
        else:
            # 默认 SQLite
            db_path = db_cfg.get("sqlite", {}).get("path", "")
            if not db_path:
                db_path = str(Path(__file__).resolve().parent.parent.parent / "data" / "novel_graphrag.db")
            return SQLiteBackend(db_path)
    except Exception:
        pass
    return SQLiteBackend()


def get_db() -> DBBackend:
    global _backend
    if _backend is None:
        _backend = _create_backend()
    return _backend


def set_db(backend: DBBackend):
    """切换到其他后端（如 MySQLBackend）"""
    global _backend
    _backend = backend
