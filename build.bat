@echo off
REM Сборка VoidZapret в один .exe со встроенной папкой zapret.

cd /d "%~dp0"

REM Используем .spec — он уже встраивает папку zapret и иконку.
pyinstaller --noconfirm VoidZapret.spec

echo.
echo === Готово! Файл: dist\VoidZapret.exe ===
pause
