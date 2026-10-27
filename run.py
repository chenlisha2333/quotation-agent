#!/usr/bin/env python3
"""
电气报价 Agent 启动脚本
一键启动 FastAPI 服务
"""

import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from app.main import app
import uvicorn

if __name__ == "__main__":
    print("=" * 50)
    print("电气报价 Agent 启动中...")
    print("=" * 50)
    print("API 文档: http://localhost:8000/docs")
    print("Web 界面: http://localhost:8000/static/index.html")
    print("=" * 50)

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["app"],
    )
