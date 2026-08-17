#!/bin/bash
echo "==> 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到 Python3，请先安装 Python 3.8 或更高版本。"
    exit 1
fi

echo "==> 创建数据目录..."
mkdir -p data

echo "==> 创建虚拟环境（可选）..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "==> 安装依赖..."
pip install -r requirements.txt

echo "==> 启动服务..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
deactivate
