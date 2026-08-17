@echo off
echo ==> 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.8+
    exit /b 1
)

echo ==> 创建数据目录...
if not exist data mkdir data

echo ==> 安装依赖...
pip install -r requirements.txt

echo ==> 启动服务...
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
