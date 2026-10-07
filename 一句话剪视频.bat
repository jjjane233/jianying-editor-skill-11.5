@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if defined JY_PYTHON (
  "%JY_PYTHON%" -X utf8 "%~dp0一句话剪视频.py" %*
  goto done
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 "%~dp0一句话剪视频.py" %*
) else (
  python -X utf8 "%~dp0一句话剪视频.py" %*
)
:done
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo 生成失败，请查看上方原因；依赖安装方式见 docs\使用文档.md。
pause
exit /b %RESULT%
