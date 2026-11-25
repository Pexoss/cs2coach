@echo off
chcp 65001 >nul
title CS2 Coach
cd /d "%~dp0"

rem --- 1) acha o Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if "%PY%"=="" where python >nul 2>nul && set "PY=python"
if "%PY%"=="" goto semPython

rem --- 2) primeira vez: instala o necessario numa pasta propria (.venv), sem mexer no resto do PC
if exist ".venv\Scripts\python.exe" goto rodar
echo Primeira vez: preparando o CS2 Coach (precisa de internet, leva 1-2 minutos)...
%PY% -m venv .venv
if errorlevel 1 goto erro
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt pystray pillow
if errorlevel 1 goto erro

:rodar
".venv\Scripts\python.exe" app\hub.py %*
if errorlevel 1 pause
exit /b 0

:semPython
echo Nao encontrei o Python neste computador.
echo Instale em https://www.python.org/downloads/ (marque "Add python.exe to PATH") e abra este arquivo de novo.
echo Ou use o instalador pronto (CS2Coach-Setup.exe), que nao precisa de Python.
start "" https://www.python.org/downloads/
pause
exit /b 1

:erro
echo.
echo Algo deu errado na preparacao. Copie a mensagem acima e envie para quem te passou o programa.
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
