@echo off
chcp 65001 >nul
cd /d %~dp0
:menu
cls
echo ==========================================
echo    Mnemochat 群聊记忆机器人 · 启动器
echo ==========================================
echo   1. 启动体检        (--check，先跑这个)
echo   2. 离线模拟器      (--sim --mock，无需密钥)
echo   3. 真实模拟器      (--sim，需要 API 密钥)
echo   4. QQ 群模式       (--onebot，需要 NapCat)
echo   5. Telegram 群模式 (--telegram)
echo   6. 人格卡修改器    (--edit-persona)
echo   7. 记忆管理台      (--inspect)
echo   0. 退出
echo ==========================================
set /p choice=请输入数字回车:
if "%choice%"=="1" .venv\Scripts\python.exe main.py --check
if "%choice%"=="2" .venv\Scripts\python.exe main.py --sim --mock
if "%choice%"=="3" .venv\Scripts\python.exe main.py --sim
if "%choice%"=="4" .venv\Scripts\python.exe main.py --onebot
if "%choice%"=="5" .venv\Scripts\python.exe main.py --telegram
if "%choice%"=="6" .venv\Scripts\python.exe main.py --edit-persona
if "%choice%"=="7" .venv\Scripts\python.exe main.py --inspect
if "%choice%"=="0" exit /b
echo.
pause
goto menu
