"""
启动入口
"""
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

import uvicorn

if __name__ == "__main__":
    os.environ.setdefault("JWT_SECRET", os.urandom(32).hex())
    print("=" * 50)
    print("  网文 GraphRAG 分析系统")
    print(f"  启动地址: http://0.0.0.0:8000")
    print("  默认管理员: admin / admin123")
    print("=" * 50)
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=False)
