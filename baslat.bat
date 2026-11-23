@echo off
rem ReqTestGen menu. Cift tiklayin.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem 1) Klasordeki tasinabilir Python (python\python.exe), 2) py launcher, 3) python
set "PY="
if exist "%~dp0python\python.exe" set PY="%~dp0python\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python bulunamadi.
  echo Cozum: Python 3.9+ kurun ya da "Windows embeddable package" zip'ini bu klasorde "python" adli
  echo klasore acin ^(python\python.exe olacak sekilde^). Ek paket gerekmez.
  pause
  exit /b 1
)

%PY% "%~dp0baslat.py"
if errorlevel 1 pause
