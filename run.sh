#!/bin/bash
# 电气报价 Agent 启动脚本

set -e

echo "=========================================="
echo "电气报价 Agent 启动中..."
echo "=========================================="

# 检查虚拟环境
if [ -d ".venv" ]; then
    echo "激活虚拟环境..."
    source .venv/bin/activate
else
    echo "警告: 未找到虚拟环境，使用系统 Python"
fi

# 检查依赖
echo "检查依赖..."
python -c "import fastapi, uvicorn, pandas, openpyxl, langgraph" 2>/dev/null || {
    echo "正在安装依赖..."
    pip install -r requirements.txt
}

echo ""
echo "启动服务..."
echo "API 文档: http://localhost:8000/docs"
echo "Web 界面: http://localhost:8000/static/index.html"
echo "=========================================="
echo ""

# 启动服务
python run.py
