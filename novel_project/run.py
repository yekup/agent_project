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
    # JWT密钥：优先从环境变量读取，不再用 os.urandom（重启后token有效）
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        jwt_secret = "novel-graphrag-dev-secret-change-in-production"
        print("[WARN] 使用默认 JWT_SECRET，生产环境请设置环境变量")
    os.environ.setdefault("JWT_SECRET", jwt_secret)

    print("=" * 50)
    print("  网文 GraphRAG 分析系统")
    print(f"  启动地址: http://0.0.0.0:8000")
    print(f"  健康检查: http://0.0.0.0:8000/health")
    print("=" * 50)
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=False)
