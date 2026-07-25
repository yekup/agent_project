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
    # JWT 密钥由 core.security.JWTHandler 统一解析:
    # 环境变量 JWT_SECRET → config.yaml → data/.jwt_secret（自动生成并持久化）。
    # 此处不再注入任何硬编码的默认密钥。
    if not os.environ.get("JWT_SECRET"):
        print("[INFO] JWT_SECRET 未设置，将使用 config.yaml 或自动生成的持久化密钥")

    print("=" * 50)
    print("  网文 GraphRAG 分析系统")
    print(f"  启动地址: http://0.0.0.0:8000")
    print(f"  健康检查: http://0.0.0.0:8000/health")
    print("=" * 50)
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=False)
