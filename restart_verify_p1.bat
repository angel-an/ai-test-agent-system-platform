@echo off
chcp 65001 >nul
echo ==========================================
echo P1 修复验证 - 重启后快速启动脚本
echo ==========================================
echo.

cd /d D:\code\Pyproject\ai-test-agent-system-platform

echo [1/3] 检查端口 8000 是否被占用...
netstat -ano | findstr ":8000" | findstr "LISTENING"
if %errorlevel% == 0 (
    echo 警告: 端口 8000 仍被占用，可能需要再次重启
    pause
    exit /b 1
)

echo.
echo [2/3] 启动 FastAPI 服务...
cd backend
start "FastAPI-8000" cmd /k "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
cd ..

echo 等待服务启动...
timeout /t 5 /nobreak >nul

echo.
echo [3/3] 运行 P1 验证...
python restart_verify_p1.py

echo.
pause
